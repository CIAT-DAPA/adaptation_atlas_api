"""Schemas of the raster endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AggregationRequest(BaseModel):
    """Temporal aggregation requested for a raster product.

    Attributes:
        type: ``none`` (source time steps), ``period`` (one value for the whole period),
            ``calendar_month``, ``fixed_window`` (consecutive windows of ``window_days``
            from ``start_date``), ``crop_season`` (from ``planting_date`` to ``end_date``
            or ``planting_date + duration_days``) or ``stages`` (explicit ``windows``).
        start_date: First day of the first fixed window (defaults to the period start).
        end_date: Last day of the fixed windows or of the crop season.
        window_days: Length of every fixed window in days (default 30).
        planting_date: Crop establishment date for ``crop_season``.
        duration_days: Season length used when ``end_date`` is omitted.
        windows: Explicit stages ``{label, start, end}`` for ``stages``.
    """

    type: str = Field(default="none", examples=["calendar_month"])
    start_date: str | None = None
    end_date: str | None = None
    window_days: int | None = Field(default=None, ge=1)
    planting_date: str | None = None
    duration_days: int | None = Field(default=None, ge=1)
    windows: list[dict[str, str]] | None = None


class RasterRequest(BaseModel):
    """Body of ``POST /datasets/{dataset_id}/raster``.

    Exactly one of ``geometry``, ``bbox`` or ``points`` selects the area. Area selections
    return a spatial file (``geotiff`` or ``netcdf``); point selections return a table
    (``json`` or ``csv``).

    Attributes:
        filters: Dataset filters (see ``GET /datasets/{dataset_id}``); the variable filter
            is always required.
        start_date: First day of the requested period (``YYYY-MM-DD``).
        end_date: Last day of the requested period (``YYYY-MM-DD``).
        geometry: GeoJSON Polygon or MultiPolygon in WGS84 (a Feature is accepted).
        bbox: ``[min_lon, min_lat, max_lon, max_lat]`` in WGS84.
        points: ``[[lon, lat], ...]`` in WGS84.
        target_crs: Target coordinate reference system (e.g. ``EPSG:3857``).
        resolution: Target pixel size in target CRS units.
        resampling: Resampling method (``nearest``, ``bilinear``, ``cubic``, ``average``).
        unit: Target unit; only registered conversions are accepted.
        aggregation: Temporal aggregation.
        format: ``geotiff``, ``netcdf``, ``json`` or ``csv``.
    """

    filters: dict[str, str | list[str]] = Field(
        default_factory=dict, examples=[{"variable": "PTOT"}]
    )
    start_date: str | None = Field(default=None, examples=["2020-01-01"])
    end_date: str | None = Field(default=None, examples=["2020-12-31"])
    geometry: dict[str, Any] | None = None
    bbox: list[float] | None = Field(default=None, examples=[[36.5, -1.5, 37.5, -0.5]])
    points: list[list[float]] | None = None
    target_crs: str | None = None
    resolution: float | None = None
    resampling: str | None = None
    unit: str | None = None
    aggregation: AggregationRequest | None = None
    format: str = Field(default="geotiff", examples=["geotiff"])

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "filters": {"variable": "PTOT"},
                    "start_date": "2020-01-01",
                    "end_date": "2020-12-31",
                    "bbox": [36.5, -1.5, 37.5, -0.5],
                    "aggregation": {"type": "calendar_month"},
                    "format": "geotiff",
                },
                {
                    "filters": {"variable": "PTOT"},
                    "start_date": "2020-03-15",
                    "end_date": "2020-07-31",
                    "points": [[36.82, -1.29], [35.28, 0.51]],
                    "aggregation": {
                        "type": "crop_season",
                        "planting_date": "2020-03-15",
                        "duration_days": 120,
                    },
                    "unit": "in",
                    "format": "json",
                },
            ]
        }
    }
