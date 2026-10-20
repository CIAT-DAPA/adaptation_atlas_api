"""Models describing a validated raster request and its outcome.

These models are shared by every raster use case: observed climate (HU-004) and climate
change scenarios (HU-003) differ only in the catalog entry that drives them, never in
the models or the pipeline.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from adaptation_atlas_api.domain.models.dataset import DatasetDefinition, VariableDefinition
from adaptation_atlas_api.domain.models.query import ValidatedFilters


class RasterFormat(str, Enum):
    """Export formats of the raster endpoint.

    Area selections (polygon or bounding box) produce a spatial product; point
    selections produce a table.
    """

    GEOTIFF = "geotiff"
    NETCDF = "netcdf"
    JSON = "json"
    CSV = "csv"


AREA_FORMATS = (RasterFormat.GEOTIFF, RasterFormat.NETCDF)
POINT_FORMATS = (RasterFormat.JSON, RasterFormat.CSV)


class SelectionKind(str, Enum):
    """Shape of the geographic request."""

    POLYGON = "polygon"
    BBOX = "bbox"
    POINTS = "points"


@dataclass(frozen=True)
class GeographicSelection:
    """A validated geographic request, always expressed in WGS84 (EPSG:4326).

    Attributes:
        kind: Shape of the request.
        geometry: GeoJSON geometry (Polygon or MultiPolygon) for polygon requests.
        bbox: ``(min_lon, min_lat, max_lon, max_lat)`` for bounding box requests.
        points: ``(lon, lat)`` pairs for point requests.
    """

    kind: SelectionKind
    geometry: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None
    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def is_area(self) -> bool:
        """Tell whether the selection covers an area rather than points.

        Returns:
            ``True`` for polygons and bounding boxes.
        """
        return self.kind is not SelectionKind.POINTS

    def describe(self) -> dict[str, Any]:
        """Render the selection for response metadata.

        Returns:
            A JSON friendly description.
        """
        # Bounding boxes are reported as their four coordinates.
        if self.kind is SelectionKind.BBOX:
            return {"type": "bbox", "bbox": list(self.bbox or ())}
        # Points are reported with their count and coordinates.
        if self.kind is SelectionKind.POINTS:
            return {"type": "points", "count": len(self.points), "points": self.points}
        return {"type": "polygon", "geometry_type": (self.geometry or {}).get("type")}


@dataclass(frozen=True)
class SpatialTransform:
    """Optional reprojection and resampling requested by the client.

    Attributes:
        target_crs: Target coordinate reference system, or ``None`` to keep the source.
        resolution: Target pixel size in target CRS units, or ``None`` to keep the source.
        resampling: Resampling method name (validated against the catalog).
    """

    target_crs: str | None = None
    resolution: float | None = None
    resampling: str = "bilinear"

    @property
    def is_identity(self) -> bool:
        """Tell whether no transformation was requested.

        Returns:
            ``True`` when neither a CRS nor a resolution was requested.
        """
        return self.target_crs is None and self.resolution is None


class AggregationType(str, Enum):
    """Temporal output requested by the client."""

    NONE = "none"
    PERIOD = "period"
    CALENDAR_MONTH = "calendar_month"
    FIXED_WINDOW = "fixed_window"
    CROP_SEASON = "crop_season"
    STAGES = "stages"


@dataclass(frozen=True)
class TemporalWindow:
    """A closed date interval over which layers are aggregated.

    Attributes:
        label: Public label of the window (e.g. ``2020-03`` or ``flowering``).
        start: First day of the window (inclusive).
        end: Last day of the window (inclusive).
    """

    label: str
    start: dt.date
    end: dt.date

    @property
    def days(self) -> int:
        """Return the length of the window in days.

        Returns:
            Number of days from ``start`` to ``end`` inclusive.
        """
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class TemporalAggregation:
    """A validated temporal aggregation request.

    Attributes:
        type: Aggregation type.
        windows: Windows to aggregate over (empty for ``none``).
        parameters: Client parameters that produced the windows, for metadata.
    """

    type: AggregationType
    windows: list[TemporalWindow] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RasterQuery:
    """A fully validated raster request ready for execution.

    Attributes:
        dataset: Dataset being queried.
        filters: Validated catalog filters (partition values, file patterns...).
        variable: Selected variable value.
        variable_definition: Physical description of the variable.
        start_date: First day requested, or ``None`` for the whole series.
        end_date: Last day requested, or ``None`` for the whole series.
        selection: Geographic request.
        transform: Requested reprojection/resampling.
        target_unit: Unit requested for the output values.
        aggregation: Requested temporal aggregation.
        output_format: Requested export format.
    """

    dataset: DatasetDefinition
    filters: ValidatedFilters
    variable: str
    variable_definition: VariableDefinition
    start_date: dt.date | None
    end_date: dt.date | None
    selection: GeographicSelection
    transform: SpatialTransform
    target_unit: str
    aggregation: TemporalAggregation
    output_format: RasterFormat


@dataclass(frozen=True)
class RasterLayer:
    """One time step of a raster series as stored (a band of a file).

    Attributes:
        uri: Resolved URI of the file.
        band: One-based band index inside the file.
        start: First day covered by the layer.
        end: Last day covered by the layer.
        label: Public label of the layer (ISO date or year-month).
    """

    uri: str
    band: int
    start: dt.date
    end: dt.date
    label: str

    @property
    def days(self) -> int:
        """Return the number of days covered by the layer.

        Returns:
            Days from ``start`` to ``end`` inclusive.
        """
        return (self.end - self.start).days + 1


@dataclass
class GridDefinition:
    """Georeferencing of a raster array.

    Attributes:
        crs: Coordinate reference system (WKT or authority string).
        transform: Affine coefficients ``(a, b, c, d, e, f)`` mapping pixel to CRS.
        width: Number of columns.
        height: Number of rows.
    """

    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return the extent of the grid as ``(min_x, min_y, max_x, max_y)``.

        Returns:
            The extent in CRS units.
        """
        a, _, c, _, e, f = self.transform
        x_edges = (c, c + a * self.width)
        y_edges = (f, f + e * self.height)
        return (min(x_edges), min(y_edges), max(x_edges), max(y_edges))

    @property
    def resolution(self) -> tuple[float, float]:
        """Return the pixel size as ``(x_size, y_size)``.

        Returns:
            Absolute pixel sizes in CRS units.
        """
        return (abs(self.transform[0]), abs(self.transform[4]))


@dataclass
class RasterStack:
    """A time series of raster layers over one grid, as a ``(time, y, x)`` float array.

    Nodata is always represented as ``NaN``.

    Attributes:
        data: Array shaped ``(layers, height, width)``.
        grid: Georeferencing shared by every layer.
        layers: Description of each slice of ``data``.
    """

    data: np.ndarray
    grid: GridDefinition
    layers: list[RasterLayer]


@dataclass
class PointSamples:
    """Values of a time series at a set of points, as a ``(time, point)`` float array.

    Attributes:
        data: Array shaped ``(layers, points)`` with ``NaN`` where no value exists.
        points: ``(lon, lat)`` pairs in WGS84, in the client's order.
        layers: Description of each row of ``data``.
    """

    data: np.ndarray
    points: list[tuple[float, float]]
    layers: list[RasterLayer]


@dataclass(frozen=True)
class ProcessingStep:
    """A transformation applied to the source data, reported to the client.

    Attributes:
        step: Machine readable step name.
        description: Human readable description.
        parameters: Parameters of the step.
    """

    step: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutputLayer:
    """One slice of the final product (a source layer or an aggregated window).

    Attributes:
        label: Public label.
        start: First day covered.
        end: Last day covered.
        coverage: Fraction of the slice period covered by source layers (0 to 1).
        source_layers: Labels of the source layers that contributed.
    """

    label: str
    start: dt.date
    end: dt.date
    coverage: float = 1.0
    source_layers: list[str] = field(default_factory=list)


@dataclass
class RasterResult:
    """Outcome of a raster query: a spatial product or point values, plus metadata.

    Attributes:
        dataset_id: Public identifier of the dataset.
        variable: Variable served.
        unit: Unit of the output values.
        applied_filters: Public filter name -> values applied.
        selection: Geographic request.
        data: Output array, ``(slice, y, x)`` for areas or ``(slice, point)`` for points.
        output_layers: Description of every slice of ``data``.
        grid: Georeferencing of area products (``None`` for point samples).
        points: Sampled points in WGS84 (empty for area products).
        aggregation: Aggregation type applied.
        steps: Every transformation applied, in order.
        source_files: Number of files read.
    """

    dataset_id: str
    variable: str
    unit: str
    applied_filters: dict[str, str | list[str]]
    selection: GeographicSelection
    data: np.ndarray
    output_layers: list[OutputLayer]
    grid: GridDefinition | None = None
    points: list[tuple[float, float]] = field(default_factory=list)
    aggregation: AggregationType = AggregationType.NONE
    steps: list[ProcessingStep] = field(default_factory=list)
    source_files: int = 0

    @property
    def is_area(self) -> bool:
        """Tell whether the result is a spatial product.

        Returns:
            ``True`` when a grid is present.
        """
        return self.grid is not None

    def describe(self) -> dict[str, Any]:
        """Render the metadata that accompanies the product.

        Returns:
            A JSON friendly dictionary describing source, selection, grid, slices and
            every processing step.
        """
        description: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "variable": self.variable,
            "unit": self.unit,
            "filters": self.applied_filters,
            "selection": self.selection.describe(),
            "aggregation": self.aggregation.value,
            "layers": [
                {
                    "label": layer.label,
                    "start": layer.start.isoformat(),
                    "end": layer.end.isoformat(),
                    "coverage": round(layer.coverage, 4),
                    "source_layers": layer.source_layers,
                }
                for layer in self.output_layers
            ],
            "processing": [
                {"step": step.step, "description": step.description, **step.parameters}
                for step in self.steps
            ],
            "source_files": self.source_files,
        }
        # Area products carry their georeferencing; point results their coordinates.
        if self.grid is not None:
            description["grid"] = {
                "crs": self.grid.crs,
                "transform": list(self.grid.transform),
                "width": self.grid.width,
                "height": self.grid.height,
                "resolution": list(self.grid.resolution),
                "bounds": list(self.grid.bounds),
            }
        else:
            description["points"] = [list(point) for point in self.points]
        return description
