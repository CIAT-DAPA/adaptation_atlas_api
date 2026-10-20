"""Unit tests for the spatial/temporal validation and unit conversion of raster requests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from adaptation_atlas_api.adapters.catalog.yaml_catalog import YamlCatalogRepository
from adaptation_atlas_api.domain.exceptions import (
    InvalidBoundingBoxError,
    InvalidCoordinatesError,
    InvalidDateRangeError,
    InvalidGeometryError,
    UnsupportedAggregationError,
    UnsupportedCrsError,
    UnsupportedResamplingError,
    UnsupportedResolutionError,
    UnsupportedUnitError,
)
from adaptation_atlas_api.domain.models.dataset import RasterDefinition
from adaptation_atlas_api.domain.models.spatial import AggregationType, SelectionKind
from adaptation_atlas_api.domain.services.unit_conversion import convert_values, resolve_target_unit
from adaptation_atlas_api.domain.validation.spatial import validate_selection, validate_transform
from adaptation_atlas_api.domain.validation.temporal import build_aggregation, validate_period

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[31.0, -1.0], [33.0, -1.0], [33.0, 1.0], [31.0, 1.0], [31.0, -1.0]]],
}


@pytest.fixture(scope="module")
def raster(catalog: YamlCatalogRepository) -> RasterDefinition:
    dataset = catalog.get("climate_observed_monthly")
    assert dataset is not None and dataset.raster is not None
    return dataset.raster


def test_selection_requires_exactly_one_shape() -> None:
    with pytest.raises(InvalidGeometryError):
        validate_selection(None, None, None, max_points=10)
    with pytest.raises(InvalidGeometryError):
        validate_selection(SQUARE, [0, 0, 1, 1], None, max_points=10)
    assert validate_selection(SQUARE, None, None, max_points=10).kind is SelectionKind.POLYGON
    assert (
        validate_selection(
            {"type": "Feature", "geometry": SQUARE}, None, None, max_points=10
        ).geometry
        == SQUARE
    )


def test_bbox_rules() -> None:
    assert validate_selection(None, [30, -5, 40, 5], None, max_points=10).bbox == (30, -5, 40, 5)
    for bad in (
        [30, -5, 40],
        [40, -5, 30, 5],
        [30, -5, 200, 5],
        ["a", 0, 1, 1],
        [0, 0, 1, float("nan")],
    ):
        with pytest.raises(InvalidBoundingBoxError):
            validate_selection(None, bad, None, max_points=10)


def test_points_rules() -> None:
    selection = validate_selection(None, None, [[36.8, -1.3], [35, 0.5]], max_points=10)
    assert selection.kind is SelectionKind.POINTS and len(selection.points) == 2
    with pytest.raises(InvalidCoordinatesError):
        validate_selection(None, None, [[200, 0]], max_points=10)
    with pytest.raises(InvalidCoordinatesError):
        validate_selection(None, None, [[1, 2, 3]], max_points=10)
    with pytest.raises(InvalidCoordinatesError):
        validate_selection(None, None, [[1, 2]] * 11, max_points=10)
    with pytest.raises(InvalidCoordinatesError):
        validate_selection(None, None, [], max_points=10)


def test_geometry_rules() -> None:
    with pytest.raises(InvalidGeometryError) as error:
        validate_selection({"type": "Point", "coordinates": [1, 2]}, None, None, max_points=10)
    assert error.value.allowed_values == ["Polygon", "MultiPolygon"]
    open_ring = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}
    with pytest.raises(InvalidGeometryError):
        validate_selection(open_ring, None, None, max_points=10)
    with pytest.raises(InvalidGeometryError):
        validate_selection({"type": "Polygon", "coordinates": []}, None, None, max_points=10)


def test_transform_rules(raster: RasterDefinition) -> None:
    transform = validate_transform(raster, None, None, None)
    assert transform.is_identity and transform.resampling == "bilinear"
    # The source CRS and resolution are a no-op.
    assert validate_transform(raster, "epsg:4326", 0.05, "nearest").is_identity
    assert validate_transform(raster, "EPSG:3857", None, "average").target_crs == "EPSG:3857"
    with pytest.raises(UnsupportedResolutionError):
        validate_transform(raster, None, -1, None)
    with pytest.raises(UnsupportedResamplingError) as error:
        validate_transform(raster, None, 0.1, "lanczos")
    assert "bilinear" in (error.value.allowed_values or [])
    restricted = raster.model_copy(
        update={"allowed_crs": ["EPSG:4326", "EPSG:3857"], "allowed_resolutions": [0.1]}
    )
    with pytest.raises(UnsupportedCrsError):
        validate_transform(restricted, "EPSG:32637", None, None)
    with pytest.raises(UnsupportedResolutionError):
        validate_transform(restricted, None, 0.2, None)
    assert validate_transform(restricted, None, 0.1, None).resolution == 0.1


def test_period_rules(raster: RasterDefinition) -> None:
    assert validate_period(raster, "2020-01-01", "2020-12-31") == (
        dt.date(2020, 1, 1),
        dt.date(2020, 12, 31),
    )
    with pytest.raises(InvalidDateRangeError):
        validate_period(raster, None, None)  # required for this dataset
    with pytest.raises(InvalidDateRangeError):
        validate_period(raster, "2020-12-31", "2020-01-01")
    with pytest.raises(InvalidDateRangeError):
        validate_period(raster, "2020/01/01", "2020-12-31")
    optional = raster.model_copy(update={"time_range_required": False})
    assert validate_period(optional, None, None) == (None, None)
    with pytest.raises(InvalidDateRangeError):
        validate_period(optional, "2020-01-01", None)


def test_aggregation_rules(raster: RasterDefinition) -> None:
    start, end = dt.date(2020, 1, 1), dt.date(2020, 3, 31)
    assert build_aggregation(raster, None, start, end).type is AggregationType.NONE
    months = build_aggregation(raster, {"type": "calendar_month"}, start, end)
    assert [w.label for w in months.windows] == ["2020-01", "2020-02", "2020-03"]
    period = build_aggregation(raster, {"type": "period"}, start, end)
    assert len(period.windows) == 1
    fixed = build_aggregation(raster, {"type": "fixed_window", "window_days": 30}, start, end)
    assert len(fixed.windows) == 4
    season = build_aggregation(
        raster,
        {"type": "crop_season", "planting_date": "2020-02-10", "duration_days": 90},
        start,
        end,
    )
    assert season.windows[0].end == dt.date(2020, 5, 9)
    stages = build_aggregation(
        raster,
        {
            "type": "stages",
            "windows": [
                {"label": "veg", "start": "2020-01-01", "end": "2020-02-15"},
                {"start": "2020-02-16", "end": "2020-03-31"},
            ],
        },
        start,
        end,
    )
    assert [w.label for w in stages.windows] == ["veg", "stage_2"]
    with pytest.raises(UnsupportedAggregationError):
        build_aggregation(raster, {"type": "weekly"}, start, end)
    with pytest.raises(UnsupportedAggregationError):
        build_aggregation(raster, {"type": "crop_season"}, start, end)
    with pytest.raises(UnsupportedAggregationError):
        build_aggregation(raster, {"type": "stages", "windows": "x"}, start, end)
    with pytest.raises(UnsupportedAggregationError):
        build_aggregation(raster, {"type": "period"}, None, None)
    disabled = raster.model_copy(update={"aggregations": ["none"]})
    with pytest.raises(UnsupportedAggregationError):
        build_aggregation(disabled, {"type": "period"}, start, end)


def test_unit_conversion(raster: RasterDefinition) -> None:
    ptot = raster.variables["PTOT"]
    assert resolve_target_unit("PTOT", ptot, None) == "mm"
    assert resolve_target_unit("PTOT", ptot, "in") == "in"
    with pytest.raises(UnsupportedUnitError) as error:
        resolve_target_unit("PTOT", ptot, "gallons")
    assert error.value.allowed_values == ["mm", "in", "cm"]
    data = np.array([[25.4, np.nan]], dtype=np.float32)
    converted, step = convert_values(data, ptot, "in")
    assert step is not None and step.step == "unit_conversion"
    assert converted[0, 0] == pytest.approx(1.0, rel=1e-4)
    assert np.isnan(converted[0, 1])
    same, no_step = convert_values(data, ptot, "mm")
    assert no_step is None and same is data
