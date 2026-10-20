"""Adapter tests for the rasterio reader, the raster service and the exporters."""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr

from adaptation_atlas_api.adapters.catalog.yaml_catalog import YamlCatalogRepository
from adaptation_atlas_api.adapters.exporters.geotiff_exporter import GeoTiffExporter
from adaptation_atlas_api.adapters.exporters.netcdf_exporter import NetCdfExporter
from adaptation_atlas_api.adapters.raster.rasterio_reader import RasterioRasterReader
from adaptation_atlas_api.domain.exceptions import (
    NoCoverageError,
    PeriodOutsideCoverageError,
    RequestTooLargeError,
    UnsupportedCombinationError,
    UnsupportedCrsError,
    UnsupportedFormatError,
    WrongDatasetTypeError,
)
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.services.point_records import point_samples_to_records
from adaptation_atlas_api.domain.services.raster_dataset import RasterDatasetService
from adaptation_atlas_api.domain.validation.filters import validate_filters

BOX = [32.0, -2.0, 34.0, 0.0]  # 4 x 4 cells of 0.5 degrees, fully inside the data area
NODATA_BOX = [30.0, 0.0, 35.0, 5.0]  # the north-west quarter: nodata


@pytest.fixture(scope="module")
def observed(catalog: YamlCatalogRepository) -> DatasetDefinition:
    dataset = catalog.get("climate_observed_monthly")
    assert dataset is not None
    return dataset


@pytest.fixture(scope="module")
def scenarios(catalog: YamlCatalogRepository) -> DatasetDefinition:
    dataset = catalog.get("climate_hazard_indices")
    assert dataset is not None
    return dataset


# --------------------------------------------------------------------------- #
# Layer discovery
# --------------------------------------------------------------------------- #
def test_layers_from_file_names(
    raster_reader: RasterioRasterReader, observed: DatasetDefinition
) -> None:
    filters = validate_filters(observed, {"variable": "PTOT"}, set())
    layers = raster_reader.list_layers(observed, filters)
    assert [layer.label for layer in layers][:3] == ["2020-01", "2020-02", "2020-03"]
    assert len(layers) == 14
    assert layers[1].start == dt.date(2020, 2, 1) and layers[1].end == dt.date(2020, 2, 29)
    assert all(layer.band == 1 for layer in layers)


def test_layers_from_bands(
    raster_reader: RasterioRasterReader, scenarios: DatasetDefinition
) -> None:
    filters = validate_filters(
        scenarios,
        {"scenario": "ssp245", "period": "2041-2060", "model": "MPI-ESM1-2-HR", "variable": "ndd"},
        set(),
    )
    layers = raster_reader.list_layers(scenarios, filters)
    assert len(layers) == 24
    assert layers[0].label == "2041-01" and layers[0].band == 1
    assert (
        layers[12].label == "2042-01" and layers[12].band == 1 and layers[12].uri != layers[0].uri
    )
    assert layers[23].band == 12


# --------------------------------------------------------------------------- #
# Service pipeline
# --------------------------------------------------------------------------- #
def test_bbox_clip_without_aggregation(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-03-01",
            "end_date": "2020-05-31",
            "bbox": BOX,
        },
    )
    assert result.is_area and result.grid is not None
    assert result.data.shape == (3, 4, 4)
    assert [layer.label for layer in result.output_layers] == ["2020-03", "2020-04", "2020-05"]
    assert result.data[0].min() == 3.0 and result.data[2].max() == 5.0
    assert result.grid.resolution == (0.5, 0.5)
    assert result.grid.bounds == pytest.approx((32.0, -2.0, 34.0, 0.0))
    assert [step.step for step in result.steps] == ["source", "clip"]
    assert result.unit == "mm" and result.source_files == 3


def test_polygon_clip_masks_outside_cells(raster_service: RasterDatasetService) -> None:
    triangle = {
        "type": "Polygon",
        "coordinates": [[[32.0, -2.0], [34.0, -2.0], [34.0, 0.0], [32.0, -2.0]]],
    }
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "geometry": triangle,
        },
    )
    assert result.data.shape == (1, 4, 4)
    # Cells outside the triangle are NaN, cells inside hold January's value.
    assert np.isnan(result.data[0, 0, 0])
    assert result.data[0, 3, 3] == 1.0


def test_calendar_month_and_unit_conversion(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-02-29",
            "bbox": BOX,
            "unit": "cm",
            "aggregation": {"type": "calendar_month"},
        },
    )
    assert [layer.label for layer in result.output_layers] == ["2020-01", "2020-02"]
    assert result.data[0, 0, 0] == pytest.approx(0.1) and result.data[1, 0, 0] == pytest.approx(0.2)
    assert result.unit == "cm"
    assert [step.step for step in result.steps] == [
        "source",
        "clip",
        "unit_conversion",
        "temporal_aggregation",
    ]


def test_crop_season_is_prorated_over_months(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "bbox": BOX,
            "aggregation": {
                "type": "crop_season",
                "planting_date": "2020-03-16",
                "duration_days": 61,
            },
        },
    )
    # 16 Mar .. 15 May: 16/31 of March (3), all of April (4), 15/31 of May (5).
    expected = 3 * 16 / 31 + 4 + 5 * 15 / 31
    [layer] = result.output_layers
    assert layer.label == "crop_season" and layer.coverage == 1.0
    assert layer.source_layers == ["2020-03", "2020-04", "2020-05"]
    assert result.data[0, 0, 0] == pytest.approx(expected, rel=1e-5)


def test_window_beyond_period_pulls_extra_layers(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-12-01",
            "end_date": "2020-12-31",
            "bbox": BOX,
            "aggregation": {
                "type": "stages",
                "windows": [{"label": "s", "start": "2020-12-01", "end": "2021-02-28"}],
            },
        },
    )
    [layer] = result.output_layers
    assert layer.source_layers == ["2020-12", "2021-01", "2021-02"]
    assert layer.coverage == 1.0
    assert result.data[0, 0, 0] == pytest.approx(12 + 1 + 2)


def test_period_mean_for_scenarios_uses_same_pipeline(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_hazard_indices",
        {
            "filters": {
                "scenario": "ssp245",
                "period": "2041-2060",
                "model": "MPI-ESM1-2-HR",
                "variable": "ndd",
            },
            "bbox": BOX,
            "aggregation": {"type": "period"},
            "start_date": "2041-01-01",
            "end_date": "2042-12-31",
        },
    )
    [layer] = result.output_layers
    assert len(layer.source_layers) == 24
    # ndd aggregates with 'sum': all 24 monthly values added.
    expected = sum((year - 2040) + month / 100 for year in (2041, 2042) for month in range(1, 13))
    assert result.data[0, 0, 0] == pytest.approx(expected, rel=1e-5)
    assert result.source_files == 2
    # Without dates the whole projected period is served (time range not required).
    whole = raster_service.query(
        "climate_hazard_indices",
        {
            "filters": {
                "scenario": "ssp245",
                "period": "2041-2060",
                "model": "MPI-ESM1-2-HR",
                "variable": "ndd",
            },
            "bbox": BOX,
        },
    )
    assert whole.data.shape[0] == 24


def test_points_are_sampled(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "SPEI-03"},
            "start_date": "2020-06-01",
            "end_date": "2020-07-31",
            "points": [[33.0, -1.0], [31.0, 4.0], [10.0, 10.0]],
            "format": "json",
        },
    )
    assert not result.is_area
    assert result.data.shape == (2, 3)
    assert result.data[0, 0] == 6.0 and result.data[1, 0] == 7.0
    # Nodata cell and point outside the file are NaN.
    assert np.isnan(result.data[0, 1]) and np.isnan(result.data[0, 2])
    records = point_samples_to_records(result)
    assert records.columns[:4] == ["point", "longitude", "latitude", "period"]
    assert records.total_count == 6
    assert records.records[0]["value"] == 6.0 and records.records[2]["value"] is None


def test_reprojection_and_resampling(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "bbox": BOX,
            "target_crs": "EPSG:3857",
            "resampling": "nearest",
        },
    )
    assert result.grid is not None and "3857" in result.grid.crs
    assert [step.step for step in result.steps] == ["source", "clip", "warp"]
    assert np.nanmax(result.data) == 1.0
    coarser = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "bbox": BOX,
            "resolution": 1.0,
            "resampling": "average",
        },
    )
    assert coarser.data.shape == (1, 2, 2)
    assert coarser.grid is not None and coarser.grid.resolution == (1.0, 1.0)
    with pytest.raises(UnsupportedCrsError):
        raster_service.query(
            "climate_observed_monthly",
            {
                "filters": {"variable": "PTOT"},
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "bbox": BOX,
                "target_crs": "EPSG:999999",
            },
        )


def test_error_paths(raster_service: RasterDatasetService) -> None:
    base = {"filters": {"variable": "PTOT"}, "start_date": "2020-01-01", "end_date": "2020-01-31"}
    with pytest.raises(NoCoverageError):
        raster_service.query("climate_observed_monthly", {**base, "bbox": [-10, -10, -5, -5]})
    with pytest.raises(PeriodOutsideCoverageError) as error:
        raster_service.query(
            "climate_observed_monthly",
            {
                "filters": {"variable": "PTOT"},
                "start_date": "1990-01-01",
                "end_date": "1990-12-31",
                "bbox": BOX,
            },
        )
    assert "2020-01-01" in error.value.message
    with pytest.raises(UnsupportedCombinationError):
        raster_service.query(
            "climate_hazard_indices",
            {
                "filters": {
                    "scenario": "ssp585",
                    "period": "2081-2100",
                    "model": "EC-Earth3",
                    "variable": "ndd",
                },
                "bbox": BOX,
            },
        )
    with pytest.raises(UnsupportedFormatError):
        raster_service.query("climate_observed_monthly", {**base, "bbox": BOX, "format": "csv"})
    with pytest.raises(UnsupportedFormatError):
        raster_service.query(
            "climate_observed_monthly", {**base, "points": [[33, -1]], "format": "geotiff"}
        )
    with pytest.raises(WrongDatasetTypeError):
        raster_service.query("hazard_exposure", {**base, "bbox": BOX})
    # A polygon entirely over nodata still returns data (NaN), not an error.
    nodata = raster_service.query("climate_observed_monthly", {**base, "bbox": NODATA_BOX})
    assert np.isnan(nodata.data).all()


def test_pixel_budget(catalog: YamlCatalogRepository, local_storage) -> None:
    tiny_reader = RasterioRasterReader(local_storage, max_pixels=10)
    service = RasterDatasetService(
        catalog, tiny_reader, max_pixels=10, max_layers=100, max_points=10
    )
    with pytest.raises(RequestTooLargeError):
        service.query(
            "climate_observed_monthly",
            {
                "filters": {"variable": "PTOT"},
                "start_date": "2020-01-01",
                "end_date": "2020-01-31",
                "bbox": BOX,
            },
        )
    few_layers = RasterDatasetService(
        catalog, tiny_reader, max_pixels=10**9, max_layers=2, max_points=10
    )
    with pytest.raises(RequestTooLargeError):
        few_layers.query(
            "climate_observed_monthly",
            {
                "filters": {"variable": "PTOT"},
                "start_date": "2020-01-01",
                "end_date": "2020-12-31",
                "bbox": BOX,
            },
        )


# --------------------------------------------------------------------------- #
# Exporters
# --------------------------------------------------------------------------- #
def test_geotiff_export_round_trip(raster_service: RasterDatasetService) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-02-29",
            "bbox": BOX,
            "aggregation": {"type": "calendar_month"},
        },
    )
    content = GeoTiffExporter().export(result)
    with rasterio.open(io.BytesIO(content)) as dataset:
        assert dataset.count == 2 and dataset.crs.to_epsg() == 4326
        assert dataset.descriptions == ("2020-01", "2020-02")
        assert dataset.tags()["UNIT"] == "mm"
        metadata = json.loads(dataset.tags()["ATLAS_METADATA"])
        assert metadata["processing"][-1]["step"] == "temporal_aggregation"
        assert dataset.tags(1)["START"] == "2020-01-01"
        assert dataset.read(2)[0, 0] == 2.0


def test_netcdf_export_round_trip(raster_service: RasterDatasetService, tmp_path: Path) -> None:
    result = raster_service.query(
        "climate_observed_monthly",
        {
            "filters": {"variable": "SPEI-03"},
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            "bbox": BOX,
        },
    )
    content = NetCdfExporter().export(result)
    target = tmp_path / "product.nc"
    target.write_bytes(content)
    with xr.open_dataset(target, engine="netcdf4") as dataset:
        variable = dataset["SPEI_03"]
        assert variable.dims == ("time", "y", "x") and variable.shape == (3, 4, 4)
        assert list(dataset["time"].values) == ["2020-01", "2020-02", "2020-03"]
        assert variable.attrs["units"] == "index"
        assert "spatial_ref" in dataset.variables
        assert float(variable[2, 0, 0]) == 3.0
        assert (
            json.loads(dataset.attrs["atlas_metadata"])["dataset_id"] == "climate_observed_monthly"
        )
