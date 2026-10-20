"""Validation of the geographic selection and spatial transformation of a raster request.

The rules here are structural (shape, ranges, vocabularies) and need no geospatial
library; geometric validity (self-intersections...) is checked by the reader adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from adaptation_atlas_api.raster.domain.models import (
    GeographicSelection,
    SelectionKind,
    SpatialTransform,
)
from adaptation_atlas_api.shared.domain.dataset import RasterDefinition
from adaptation_atlas_api.shared.domain.exceptions import (
    DomainError,
    InvalidBoundingBoxError,
    InvalidCoordinatesError,
    InvalidGeometryError,
    UnsupportedCrsError,
    UnsupportedResamplingError,
    UnsupportedResolutionError,
)

SUPPORTED_GEOMETRY_TYPES = ("Polygon", "MultiPolygon")
LON_RANGE = (-180.0, 180.0)
LAT_RANGE = (-90.0, 90.0)


def _as_float(value: Any, parameter: str, error: type[DomainError]) -> float:
    """Convert a value to float, raising the given domain error when impossible.

    Args:
        value: Raw value.
        parameter: Parameter name for the error.
        error: Domain error class to raise.

    Returns:
        The numeric value.
    """
    # Booleans are numbers in Python but never valid coordinates.
    if isinstance(value, bool):
        raise error(f"Parameter '{parameter}' contains a non-numeric value.", parameter=parameter)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise error(
            f"Parameter '{parameter}' contains a non-numeric value.", parameter=parameter
        ) from exc
    # NaN and infinities are numeric in Python but meaningless as coordinates.
    if number != number or number in (float("inf"), float("-inf")):
        raise error(f"Parameter '{parameter}' contains a non-finite value.", parameter=parameter)
    return number


def validate_point(value: Any, parameter: str) -> tuple[float, float]:
    """Validate one ``[lon, lat]`` pair.

    Args:
        value: Raw pair.
        parameter: Parameter name for error messages.

    Returns:
        The ``(lon, lat)`` tuple.

    Raises:
        InvalidCoordinatesError: If the pair is malformed or out of range.
    """
    # A point is exactly two numbers.
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 2:
        raise InvalidCoordinatesError(
            f"Parameter '{parameter}' must hold [longitude, latitude] pairs.",
            parameter=parameter,
        )
    lon = _as_float(value[0], parameter, InvalidCoordinatesError)
    lat = _as_float(value[1], parameter, InvalidCoordinatesError)
    # Coordinates must be geographic (WGS84) ranges.
    if not (LON_RANGE[0] <= lon <= LON_RANGE[1]) or not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
        raise InvalidCoordinatesError(
            f"Coordinate ({lon}, {lat}) is outside the accepted ranges "
            "(longitude -180..180, latitude -90..90).",
            parameter=parameter,
        )
    return lon, lat


def validate_bbox(value: Any) -> tuple[float, float, float, float]:
    """Validate a ``[min_lon, min_lat, max_lon, max_lat]`` bounding box.

    Args:
        value: Raw bounding box.

    Returns:
        The validated tuple.

    Raises:
        InvalidBoundingBoxError: If values are missing, non-numeric, reversed or out of
            range.
    """
    # A bounding box is exactly four numbers.
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 4:
        raise InvalidBoundingBoxError(
            "Parameter 'bbox' must be [min_lon, min_lat, max_lon, max_lat].", parameter="bbox"
        )
    numbers = [_as_float(item, "bbox", InvalidBoundingBoxError) for item in value]
    min_lon, min_lat, max_lon, max_lat = numbers
    # Reversed boxes are rejected explicitly rather than silently swapped.
    if min_lon >= max_lon or min_lat >= max_lat:
        raise InvalidBoundingBoxError(
            "Parameter 'bbox' is reversed or empty: minimums must be lower than maximums.",
            parameter="bbox",
        )
    # The box must lie inside geographic ranges.
    if (
        min_lon < LON_RANGE[0]
        or max_lon > LON_RANGE[1]
        or min_lat < LAT_RANGE[0]
        or max_lat > LAT_RANGE[1]
    ):
        raise InvalidBoundingBoxError(
            "Parameter 'bbox' is outside the accepted ranges "
            "(longitude -180..180, latitude -90..90).",
            parameter="bbox",
        )
    return min_lon, min_lat, max_lon, max_lat


def _validate_ring(ring: Any) -> None:
    """Validate one linear ring of a polygon.

    Args:
        ring: Raw ring coordinates.

    Raises:
        InvalidGeometryError: If the ring has fewer than four positions or is not closed.
    """
    # A ring needs at least four positions, the last repeating the first.
    if not isinstance(ring, Sequence) or len(ring) < 4:
        raise InvalidGeometryError(
            "Polygon rings need at least four positions.", parameter="geometry"
        )
    positions = [validate_point(position, "geometry") for position in ring]
    # GeoJSON rings must be closed.
    if positions[0] != positions[-1]:
        raise InvalidGeometryError("Polygon rings must be closed.", parameter="geometry")


def validate_geometry(value: Any) -> dict[str, Any]:
    """Validate a GeoJSON Polygon or MultiPolygon (a Feature wrapper is unwrapped).

    Args:
        value: Raw GeoJSON object.

    Returns:
        The geometry dictionary.

    Raises:
        InvalidGeometryError: If the object is not a supported, well formed geometry.
    """
    # A GeoJSON object must be a mapping.
    if not isinstance(value, dict):
        raise InvalidGeometryError(
            "Parameter 'geometry' must be a GeoJSON Polygon or MultiPolygon.",
            parameter="geometry",
        )
    geometry = value
    # Features are accepted for convenience; only their geometry is used.
    if value.get("type") == "Feature":
        geometry = value.get("geometry") or {}
    geometry_type = geometry.get("type")
    # Only area geometries can clip a raster.
    if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
        raise InvalidGeometryError(
            f"Geometry type '{geometry_type}' is not supported.",
            parameter="geometry",
            allowed_values=list(SUPPORTED_GEOMETRY_TYPES),
        )
    coordinates = geometry.get("coordinates")
    # Empty geometries cannot select anything.
    if not coordinates:
        raise InvalidGeometryError("Geometry has no coordinates.", parameter="geometry")
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    # Validate every ring of every polygon.
    for polygon in polygons:
        # A polygon is a list of rings.
        if not isinstance(polygon, Sequence) or not polygon:
            raise InvalidGeometryError("Polygon has no rings.", parameter="geometry")
        for ring in polygon:
            _validate_ring(ring)
    return {"type": geometry_type, "coordinates": coordinates}


def validate_selection(
    geometry: Any, bbox: Any, points: Any, *, max_points: int
) -> GeographicSelection:
    """Validate that exactly one geographic request was supplied and parse it.

    Args:
        geometry: Raw GeoJSON geometry, or ``None``.
        bbox: Raw bounding box, or ``None``.
        points: Raw list of ``[lon, lat]`` pairs, or ``None``.
        max_points: Maximum number of points accepted in one request.

    Returns:
        The validated selection.

    Raises:
        InvalidGeometryError: If none or several selections are given.
        InvalidCoordinatesError: If too many points are given.
    """
    supplied = [
        name
        for name, value in (("geometry", geometry), ("bbox", bbox), ("points", points))
        if value is not None
    ]
    # Exactly one way of selecting the area is accepted per request.
    if len(supplied) != 1:
        raise InvalidGeometryError(
            "Exactly one of 'geometry', 'bbox' or 'points' must be supplied.",
            parameter="geometry",
            allowed_values=["geometry", "bbox", "points"],
        )
    # Polygons are validated ring by ring.
    if geometry is not None:
        return GeographicSelection(SelectionKind.POLYGON, geometry=validate_geometry(geometry))
    # Bounding boxes are validated as four ordered numbers.
    if bbox is not None:
        return GeographicSelection(SelectionKind.BBOX, bbox=validate_bbox(bbox))
    # Points are validated one by one.
    if not isinstance(points, Sequence) or isinstance(points, str) or not points:
        raise InvalidCoordinatesError(
            "Parameter 'points' must be a non-empty list of [longitude, latitude] pairs.",
            parameter="points",
        )
    # Too many points would turn a sampling request into a bulk extraction.
    if len(points) > max_points:
        raise InvalidCoordinatesError(
            f"Parameter 'points' accepts at most {max_points} coordinates.", parameter="points"
        )
    return GeographicSelection(
        SelectionKind.POINTS, points=[validate_point(point, "points") for point in points]
    )


def validate_transform(
    raster: RasterDefinition,
    target_crs: str | None,
    resolution: float | None,
    resampling: str | None,
) -> SpatialTransform:
    """Validate the requested reprojection/resampling against the catalog.

    Args:
        raster: Raster description of the dataset.
        target_crs: Requested CRS, or ``None``.
        resolution: Requested pixel size, or ``None``.
        resampling: Requested resampling method, or ``None`` for the default.

    Returns:
        The validated transform.

    Raises:
        UnsupportedCrsError: If the CRS is not permitted.
        UnsupportedResolutionError: If the resolution is not permitted or not positive.
        UnsupportedResamplingError: If the method is not permitted.
    """
    crs = target_crs.strip() if target_crs and target_crs.strip() else None
    # A target CRS equal to the source is the same as no reprojection.
    if crs is not None and crs.upper() == raster.crs.upper():
        crs = None
    # The catalog may restrict the CRS clients can request.
    if (
        crs is not None
        and raster.allowed_crs is not None
        and crs.upper() not in {item.upper() for item in raster.allowed_crs}
    ):
        raise UnsupportedCrsError(
            f"Coordinate reference system '{crs}' is not supported by the dataset.",
            parameter="target_crs",
            allowed_values=raster.allowed_crs,
        )
    # Resolutions must be positive and, when restricted, in the permitted list.
    if resolution is not None:
        if resolution <= 0:
            raise UnsupportedResolutionError(
                "Parameter 'resolution' must be positive.", parameter="resolution"
            )
        if raster.allowed_resolutions is not None and resolution not in raster.allowed_resolutions:
            raise UnsupportedResolutionError(
                f"Resolution {resolution} is not permitted for the dataset.",
                parameter="resolution",
                allowed_values=[str(item) for item in raster.allowed_resolutions],
            )
        # Asking for the source resolution in the source CRS is a no-op.
        if crs is None and resolution == raster.resolution:
            resolution = None
    method = (resampling or "").strip().lower() or raster.default_resampling
    # Only registered resampling methods are accepted.
    if method not in raster.resampling_methods:
        raise UnsupportedResamplingError(
            f"Resampling method '{resampling}' is not supported.",
            parameter="resampling",
            allowed_values=raster.resampling_methods,
        )
    return SpatialTransform(target_crs=crs, resolution=resolution, resampling=method)
