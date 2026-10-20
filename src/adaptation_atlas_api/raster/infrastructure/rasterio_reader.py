"""Raster reader backed by rasterio/GDAL.

Layers are discovered by listing the storage with the dataset path template and
recovering each layer's date from the file name and/or the band descriptions, as
declared by the catalog ``time_axis``. Reads use GDAL range requests, so clipping a
tiled GeoTIFF in S3 only downloads the blocks covering the selection.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Iterator
from typing import Any

import numpy as np
import rasterio
import rasterio.errors
import rasterio.features
import rasterio.mask
import rasterio.warp
import rasterio.windows
from rasterio.crs import CRS
from rasterio.enums import Resampling
from shapely.geometry import box, shape

from adaptation_atlas_api.raster.domain.models import (
    GeographicSelection,
    GridDefinition,
    PointSamples,
    RasterLayer,
    RasterStack,
    SelectionKind,
    SpatialTransform,
)
from adaptation_atlas_api.raster.domain.temporal_windows import month_end
from adaptation_atlas_api.raster.ports.raster_reader import RasterReader
from adaptation_atlas_api.shared.domain.dataset import (
    DatasetDefinition,
    RasterDefinition,
    TemporalResolution,
)
from adaptation_atlas_api.shared.domain.exceptions import (
    InvalidGeometryError,
    NoCoverageError,
    QueryExecutionError,
    RequestTooLargeError,
    SourceUnavailableError,
    UnsupportedCrsError,
)
from adaptation_atlas_api.shared.domain.filters import ValidatedFilters
from adaptation_atlas_api.shared.domain.storage_paths import (
    render_storage_paths,
    static_prefix_of,
)
from adaptation_atlas_api.shared.infrastructure.partition_index import pattern_to_regex
from adaptation_atlas_api.shared.ports.storage import Storage

logger = logging.getLogger(__name__)

WGS84 = CRS.from_epsg(4326)


def layer_bounds(
    resolution: TemporalResolution, parts: dict[str, int]
) -> tuple[dt.date, dt.date, str]:
    """Compute the period covered by a layer from its date components.

    Args:
        resolution: Temporal resolution of the dataset.
        parts: ``year`` and, depending on the resolution, ``month`` and ``day``.

    Returns:
        The ``(start, end, label)`` of the layer.
    """
    year = parts["year"]
    # Annual layers span the calendar year.
    if resolution is TemporalResolution.ANNUAL:
        return dt.date(year, 1, 1), dt.date(year, 12, 31), f"{year:04d}"
    month = parts["month"]
    # Monthly layers span the calendar month.
    if resolution is TemporalResolution.MONTHLY:
        return dt.date(year, month, 1), month_end(year, month), f"{year:04d}-{month:02d}"
    day = dt.date(year, month, parts["day"])
    return day, day, day.isoformat()


def _date_parts(match: Any) -> dict[str, int]:
    """Extract the integer date components captured by a pattern match.

    Args:
        match: Regular expression match with optional ``year``/``month``/``day`` groups.

    Returns:
        Component name -> integer value for the groups present.
    """
    parts: dict[str, int] = {}
    # Only the date groups are relevant; partition groups are ignored.
    for name in ("year", "month", "day"):
        value = match.groupdict().get(name)
        if value is not None:
            parts[name] = int(value)
    return parts


class RasterioRasterReader(RasterReader):
    """Reads Hive-partitioned GeoTIFF series with rasterio."""

    def __init__(self, storage: Storage, *, max_pixels: int = 50_000_000) -> None:
        """Create the reader.

        Args:
            storage: Storage adapter resolving URIs, listing files and providing GDAL options.
            max_pixels: Maximum number of cells (layers x rows x columns) read per request.
        """
        self._storage = storage
        self._max_pixels = max_pixels
        self._band_cache: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def list_layers(
        self, dataset: DatasetDefinition, filters: ValidatedFilters
    ) -> list[RasterLayer]:
        """Find every layer matching the filters, reading dates from names and bands.

        Args:
            dataset: Raster dataset.
            filters: Validated filters.

        Returns:
            Layers sorted chronologically.
        """
        raster = dataset.raster
        assert raster is not None  # noqa: S101 - only raster datasets reach the reader
        file_regex = (
            pattern_to_regex(raster.time_axis.file_pattern)
            if raster.time_axis.file_pattern
            else None
        )
        band_regex = (
            pattern_to_regex(raster.time_axis.band_pattern)
            if raster.time_axis.band_pattern
            else None
        )
        layers: list[RasterLayer] = []

        # Each rendered path (one per partition combination) is listed and matched.
        for path in render_storage_paths(dataset, filters):
            path_regex = pattern_to_regex(path)
            prefix = static_prefix_of(path)
            directory = prefix[: prefix.rfind("/") + 1] if "/" in prefix else ""
            # Keys matching the rendered glob are candidate files.
            for key in self._list_keys(directory):
                # The whole key must match the rendered path (wildcards included).
                if not path_regex.fullmatch(key):
                    continue
                file_name = key.rsplit("/", 1)[-1]
                parts: dict[str, int] = {}
                # The file name may carry some date components.
                if file_regex is not None:
                    match = file_regex.fullmatch(file_name)
                    # Files that do not follow the time pattern are not layers.
                    if match is None:
                        continue
                    parts = _date_parts(match)
                layers.extend(self._layers_of_file(raster, key, parts, band_regex))
        layers.sort(key=lambda item: (item.start, item.uri, item.band))
        return layers

    def _list_keys(self, directory: str) -> list[str]:
        """List the GeoTIFF keys under a directory prefix.

        Args:
            directory: Directory prefix relative to the storage root.

        Returns:
            Matching keys.

        Raises:
            SourceUnavailableError: If the storage cannot be listed.
        """
        try:
            return self._storage.list_files(directory, suffix=".tif")
        except Exception as error:  # noqa: BLE001 - any listing failure is a storage failure
            logger.error("Could not list raster files under '%s': %s", directory, error)
            raise SourceUnavailableError("The raster source could not be listed.") from error

    def _layers_of_file(
        self,
        raster: RasterDefinition,
        key: str,
        file_parts: dict[str, int],
        band_regex: Any,
    ) -> list[RasterLayer]:
        """Build the layers held by one file.

        Args:
            raster: Raster description of the dataset.
            key: File key relative to the storage root.
            file_parts: Date components recovered from the file name.
            band_regex: Compiled band description pattern, or ``None`` for single layers.

        Returns:
            One layer per matching band (or a single layer for band 1).
        """
        uri = self._storage.resolve_uri(key)
        # Without a band pattern the file is a single layer dated by its name.
        if band_regex is None:
            start, end, label = layer_bounds(raster.temporal_resolution, file_parts)
            return [RasterLayer(uri, 1, start, end, label)]

        layers: list[RasterLayer] = []
        # Each band whose description matches the pattern is a layer.
        for position, description in enumerate(self._band_descriptions(uri), start=1):
            match = band_regex.fullmatch(description or "")
            # Bands without a dated description are not time steps.
            if match is None:
                continue
            parts = {**file_parts, **_date_parts(match)}
            start, end, label = layer_bounds(raster.temporal_resolution, parts)
            layers.append(RasterLayer(uri, position, start, end, label))
        return layers

    def _band_descriptions(self, uri: str) -> list[str]:
        """Read (and cache) the band descriptions of a file.

        Args:
            uri: Resolved file URI.

        Returns:
            One description per band (empty strings when absent).
        """
        with self._lock:
            cached = self._band_cache.get(uri)
        # Files are immutable, so descriptions are read once per process.
        if cached is not None:
            return cached
        with self._open(uri) as dataset:
            descriptions = [item or "" for item in dataset.descriptions]
        with self._lock:
            self._band_cache[uri] = descriptions
        return descriptions

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #
    def read_area(
        self,
        dataset: DatasetDefinition,
        layers: list[RasterLayer],
        selection: GeographicSelection,
        transform: SpatialTransform,
    ) -> RasterStack:
        """Clip every layer to the selection and warp the stack when requested.

        Args:
            dataset: Raster dataset.
            layers: Layers to read in output order.
            selection: Polygon or bounding box in WGS84.
            transform: Reprojection/resampling to apply.

        Returns:
            The clipped (and warped) stack with ``NaN`` as nodata.
        """
        geometry = self._selection_geometry(selection)
        slices: list[np.ndarray] = []
        grid: GridDefinition | None = None

        with rasterio.Env(**self._storage.gdal_options()):
            # Files are opened once; every band of the file is read from the same window.
            for uri, bands in self._group_by_file(layers):
                with self._open(uri) as source:
                    data, source_grid = self._read_window(source, bands, geometry, len(layers))
                # Every file must share the grid of the first one.
                if grid is None:
                    grid = source_grid
                elif (source_grid.width, source_grid.height, source_grid.transform) != (
                    grid.width,
                    grid.height,
                    grid.transform,
                ):
                    raise QueryExecutionError(
                        "The source layers of the dataset do not share the same grid."
                    )
                slices.append(data)
            stack = np.concatenate(slices, axis=0)
            assert grid is not None  # noqa: S101 - at least one layer was read
            # Reprojection/resampling is applied once to the whole stack.
            if not transform.is_identity:
                stack, grid = self._warp(stack, grid, transform)
        return RasterStack(data=stack, grid=grid, layers=list(layers))

    def read_points(
        self, dataset: DatasetDefinition, layers: list[RasterLayer], selection: GeographicSelection
    ) -> PointSamples:
        """Sample every layer at the selected points.

        Args:
            dataset: Raster dataset.
            layers: Layers to sample in output order.
            selection: Points in WGS84.

        Returns:
            Sampled values with ``NaN`` outside the coverage or on nodata.
        """
        values = np.full((len(layers), len(selection.points)), np.nan, dtype=np.float32)
        position = 0

        with rasterio.Env(**self._storage.gdal_options()):
            # Each file is opened once and sampled for all its requested bands.
            for uri, bands in self._group_by_file(layers):
                with self._open(uri) as source:
                    xs, ys = self._to_source_crs(source, selection.points)
                    samples = source.sample(
                        list(zip(xs, ys, strict=True)), indexes=bands, masked=True
                    )
                    # ``sample`` yields one masked array of band values per point.
                    for point_index, sample in enumerate(samples):
                        filled = np.ma.filled(sample.astype(np.float32), np.nan)
                        values[position : position + len(bands), point_index] = filled
                position += len(bands)
        return PointSamples(data=values, points=list(selection.points), layers=list(layers))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _open(self, uri: str) -> Any:
        """Open a raster, translating I/O failures into controlled errors.

        Args:
            uri: Resolved file URI.

        Returns:
            An open rasterio dataset (context manager).

        Raises:
            SourceUnavailableError: If the file cannot be opened.
        """
        try:
            return rasterio.open(uri)
        except rasterio.errors.RasterioIOError as error:
            logger.error("Could not open raster '%s': %s", uri, error)
            raise SourceUnavailableError("The raster source is currently unavailable.") from error

    @staticmethod
    def _group_by_file(layers: list[RasterLayer]) -> Iterator[tuple[str, list[int]]]:
        """Group consecutive layers of the same file, preserving the output order.

        Args:
            layers: Layers in output order.

        Yields:
            ``(uri, bands)`` pairs.
        """
        current_uri: str | None = None
        bands: list[int] = []
        # Consecutive layers of one file become one read.
        for layer in layers:
            # A new file flushes the previous group.
            if layer.uri != current_uri and bands:
                yield str(current_uri), bands
                bands = []
            current_uri = layer.uri
            bands.append(layer.band)
        # The last group is flushed at the end.
        if bands:
            yield str(current_uri), bands

    @staticmethod
    def _selection_geometry(selection: GeographicSelection) -> dict[str, Any]:
        """Turn the selection into a GeoJSON geometry in WGS84, checking its validity.

        Args:
            selection: Polygon or bounding box selection.

        Returns:
            The GeoJSON geometry.

        Raises:
            InvalidGeometryError: If the polygon is geometrically invalid.
        """
        # Bounding boxes become rectangles.
        if selection.kind is SelectionKind.BBOX:
            return box(*selection.bbox).__geo_interface__  # type: ignore[misc]
        geometry = shape(selection.geometry)
        # Self-intersecting or degenerate polygons cannot clip a raster.
        if not geometry.is_valid or geometry.is_empty:
            raise InvalidGeometryError("The polygon geometry is invalid.", parameter="geometry")
        return dict(selection.geometry or {})

    def _read_window(
        self, source: Any, bands: list[int], geometry: dict[str, Any], total_layers: int
    ) -> tuple[np.ndarray, GridDefinition]:
        """Read the bands of an open file clipped to the geometry.

        Args:
            source: Open rasterio dataset.
            bands: One-based bands to read.
            geometry: GeoJSON geometry in WGS84.
            total_layers: Number of layers of the whole request (for the pixel budget).

        Returns:
            The clipped float32 array ``(bands, rows, cols)`` and its grid.

        Raises:
            NoCoverageError: If the geometry does not intersect the file.
            RequestTooLargeError: If the clipped window exceeds the pixel budget.
        """
        # The geometry is expressed in the file CRS before clipping.
        local_geometry = geometry
        if source.crs is not None and source.crs != WGS84:
            local_geometry = rasterio.warp.transform_geom(WGS84, source.crs, geometry)
        try:
            window = rasterio.features.geometry_window(source, [local_geometry])
        except rasterio.errors.WindowError as error:
            raise NoCoverageError(
                "The geographic selection does not intersect the dataset coverage.",
                parameter="geometry",
            ) from error
        # The budget is checked before any pixel is downloaded.
        cells = int(window.height) * int(window.width) * total_layers
        if cells > self._max_pixels:
            raise RequestTooLargeError(
                f"The request would read {cells} cells; the limit is {self._max_pixels}. "
                "Reduce the area or the period, or aggregate over time.",
                parameter="bbox",
            )
        try:
            masked, out_transform = rasterio.mask.mask(
                source, [local_geometry], crop=True, indexes=bands, filled=False
            )
        except ValueError as error:
            raise NoCoverageError(
                "The geographic selection does not intersect the dataset coverage.",
                parameter="geometry",
            ) from error
        data = np.ma.filled(masked.astype(np.float32), np.nan)
        # Files with an explicit numeric nodata still need it mapped to NaN.
        if source.nodata is not None and not np.isnan(source.nodata):
            data[data == np.float32(source.nodata)] = np.nan
        grid = GridDefinition(
            crs=source.crs.to_string() if source.crs else "",
            transform=tuple(out_transform)[:6],
            width=int(data.shape[2]),
            height=int(data.shape[1]),
        )
        return data, grid

    def _warp(
        self, stack: np.ndarray, grid: GridDefinition, transform: SpatialTransform
    ) -> tuple[np.ndarray, GridDefinition]:
        """Reproject and/or resample the stack to the requested grid.

        Args:
            stack: Array ``(layers, rows, cols)`` with ``NaN`` as nodata.
            grid: Georeferencing of ``stack``.
            transform: Target CRS, resolution and resampling method.

        Returns:
            The warped stack and its grid.

        Raises:
            UnsupportedCrsError: If the target CRS cannot be interpreted.
            RequestTooLargeError: If the target grid exceeds the pixel budget.
        """
        src_crs = CRS.from_user_input(grid.crs)
        try:
            dst_crs = CRS.from_user_input(transform.target_crs) if transform.target_crs else src_crs
        except rasterio.errors.CRSError as error:
            raise UnsupportedCrsError(
                f"Coordinate reference system '{transform.target_crs}' is not recognised.",
                parameter="target_crs",
            ) from error
        src_transform = rasterio.Affine(*grid.transform)
        dst_transform, width, height = rasterio.warp.calculate_default_transform(
            src_crs,
            dst_crs,
            grid.width,
            grid.height,
            *grid.bounds,
            resolution=transform.resolution,
        )
        # The budget also bounds the warped output.
        cells = int(width) * int(height) * stack.shape[0]
        if cells > self._max_pixels:
            raise RequestTooLargeError(
                f"The requested grid would hold {cells} cells; the limit is {self._max_pixels}.",
                parameter="resolution",
            )
        destination = np.full((stack.shape[0], int(height), int(width)), np.nan, dtype=np.float32)
        rasterio.warp.reproject(
            source=stack,
            destination=destination,
            src_transform=src_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=Resampling[transform.resampling],
        )
        warped_grid = GridDefinition(
            crs=dst_crs.to_string(),
            transform=tuple(dst_transform)[:6],
            width=int(width),
            height=int(height),
        )
        return destination, warped_grid

    @staticmethod
    def _to_source_crs(
        source: Any, points: list[tuple[float, float]]
    ) -> tuple[list[float], list[float]]:
        """Project WGS84 points into the CRS of an open file.

        Args:
            source: Open rasterio dataset.
            points: ``(lon, lat)`` pairs.

        Returns:
            The ``(xs, ys)`` lists in the file CRS.
        """
        lons = [point[0] for point in points]
        lats = [point[1] for point in points]
        # Files already in WGS84 need no projection.
        if source.crs is None or source.crs == WGS84:
            return lons, lats
        xs, ys = rasterio.warp.transform(WGS84, source.crs, lons, lats)
        return list(xs), list(ys)
