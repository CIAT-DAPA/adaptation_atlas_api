"""Tests of the project catalog entries against synthetic files, covering typed filters,
filters on absent columns, excluded columns and derived predicates on absent columns."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from adaptation_atlas_api.adapters.catalog.yaml_catalog import YamlCatalogRepository
from adaptation_atlas_api.adapters.tabular.duckdb_reader import (
    DuckDBTabularReader,
    _typed_placeholder,
)
from adaptation_atlas_api.domain.exceptions import FilterNotApplicableError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.models.query import OutputFormat, TabularQuery
from adaptation_atlas_api.domain.validation.filters import validate_filters

CONTROL = {"format", "limit", "offset"}


def make_query(dataset: DatasetDefinition, filters: dict[str, str]) -> TabularQuery:
    validated = validate_filters(dataset, filters, CONTROL)
    return TabularQuery(
        dataset=dataset, filters=validated, limit=1000, offset=0, output_format=OutputFormat.JSON
    )


def get(catalog: YamlCatalogRepository, dataset_id: str) -> DatasetDefinition:
    dataset = catalog.get(dataset_id)
    assert dataset is not None
    return dataset


def test_every_project_dataset_is_valid(catalog: YamlCatalogRepository) -> None:
    ids = {dataset.id for dataset in catalog.list_all()}
    assert {
        "hazard_exposure",
        "climate_observed_admin",
        "climate_hazard_change",
        "climate_season_ensemble",
        "exposure_crop_livestock",
        "exposure_population_worldpop",
        "exposure_population_kenya_census",
        "exposure_population_kenya_projections",
        "exposure_kenya_flood_intersect",
        "impacts_pest_disease",
        "impacts_gyga_yield",
        "phenology_seasonal",
        "socioeconomic_landuse_fao",
        "socioeconomic_gdp_sector",
        "socioeconomic_poverty_rates",
        "socioeconomic_faostat",
        "socioeconomic_acled_conflicts",
    } <= ids
    # Every dataset declares a domain so the catalog listing can be filtered.
    assert all(dataset.domain for dataset in catalog.list_all())


def test_typed_placeholder() -> None:
    assert _typed_placeholder("VARCHAR") == "?"
    assert _typed_placeholder("INTEGER") == "CAST(? AS INTEGER)"
    assert _typed_placeholder("DOUBLE") == "CAST(? AS DOUBLE)"
    assert _typed_placeholder("DATE") == "CAST(? AS DATE)"


def test_integer_column_filter_from_text_value(
    reader: DuckDBTabularReader, catalog: YamlCatalogRepository
) -> None:
    observed = get(catalog, "climate_observed_admin")
    result = reader.query(
        make_query(observed, {"country": "KEN", "admin_level": "1", "year": "2020", "month": "2"})
    )
    assert result.records
    assert all(row["year"] == 2020 and row["month"] == 2 for row in result.records)
    assert reader.query(make_query(observed, {"country": "KEN", "year": "1999"})).records == []


def test_filter_on_absent_column_is_rejected(
    reader: DuckDBTabularReader, catalog: YamlCatalogRepository
) -> None:
    hazard_change = get(catalog, "climate_hazard_change")
    # ``severity`` exists in the frequency table but not in the precipitation change table.
    with pytest.raises(FilterNotApplicableError) as error:
        reader.query(
            make_query(
                hazard_change,
                {"country": "KEN", "indicator": "ptot_change_ensemble", "severity": "severe"},
            )
        )
    assert error.value.parameter == "severity"


def test_derived_predicates_on_absent_columns(
    reader: DuckDBTabularReader, catalog: YamlCatalogRepository
) -> None:
    hazard_change = get(catalog, "climate_hazard_change")
    base = {"country": "KEN", "indicator": "ptot_change_ensemble"}
    # The change table has no admin2_name: level 0 and 1 rows still resolve correctly.
    level0 = reader.query(make_query(hazard_change, {**base, "admin_level": "0"}))
    level1 = reader.query(make_query(hazard_change, {**base, "admin_level": "1"}))
    level2 = reader.query(make_query(hazard_change, {**base, "admin_level": "2"}))
    assert [row["direction"] for row in level0.records] == ["increase_5"]
    assert [row["admin1_name"] for row in level1.records] == ["Nakuru"]
    assert level2.records == [] and level2.total_count == 0


def test_nan_values_become_null(
    reader: DuckDBTabularReader, catalog: YamlCatalogRepository
) -> None:
    hazard_change = get(catalog, "climate_hazard_change")
    result = reader.query(
        make_query(hazard_change, {"country": "ETH", "indicator": "haz_freq_ensemble"})
    )
    means = [row["mean"] for row in result.records]
    assert None in means
    assert not any(isinstance(value, float) and math.isnan(value) for value in means)


def test_excluded_columns_are_not_returned(
    reader: DuckDBTabularReader, catalog: YamlCatalogRepository
) -> None:
    gyga = get(catalog, "impacts_gyga_yield")
    result = reader.query(make_query(gyga, {"crop": "bean", "admin1": "Nakuru"}))
    assert result.total_count == 1
    assert "geometry" not in result.columns
    assert "geometry_bbox" not in result.columns
    assert result.records[0]["yield_rfd"] == 1.2


def test_endpoint_reports_filter_not_applicable(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/climate_hazard_change/records",
        params={"country": "KEN", "indicator": "ptot_change_ensemble", "severity": "severe"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "filter_not_applicable"
    assert response.json()["parameter"] == "severity"


def test_endpoint_lists_domains(client: TestClient) -> None:
    domains = {item["domain"] for item in client.get("/api/v1/datasets").json()}
    assert {
        "climate",
        "exposure",
        "impacts",
        "phenology",
        "socioeconomic",
        "hazard_exposure",
    } <= domains
    climate = client.get("/api/v1/datasets", params={"domain": "climate"}).json()
    assert {item["id"] for item in climate} >= {"climate_observed_admin", "climate_hazard_change"}
