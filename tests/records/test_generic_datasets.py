"""Adapter and endpoint tests for catalog patterns beyond hazard exposure.

Covers single-file datasets, partition values embedded in file names and datasets
without explicit response fields.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from adaptation_atlas_api.records.domain.query import OutputFormat, TabularQuery
from adaptation_atlas_api.records.infrastructure.duckdb_reader import DuckDBTabularReader
from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition
from adaptation_atlas_api.shared.domain.filter_validation import validate_filters
from adaptation_atlas_api.shared.infrastructure.local_storage import LocalStorage
from adaptation_atlas_api.shared.infrastructure.partition_index import (
    StoragePartitionIndex,
    pattern_to_regex,
)
from adaptation_atlas_api.shared.infrastructure.yaml_catalog import YamlCatalogRepository

CONTROL = {"format", "limit", "offset"}


def make_query(dataset: DatasetDefinition, filters: dict[str, str]) -> TabularQuery:
    validated = validate_filters(dataset, filters, CONTROL)
    return TabularQuery(
        dataset=dataset, filters=validated, limit=1000, offset=0, output_format=OutputFormat.JSON
    )


@pytest.fixture(scope="session")
def phenology(catalog: YamlCatalogRepository) -> DatasetDefinition:
    dataset = catalog.get("phenology_seasonal_test")
    assert dataset is not None
    return dataset


@pytest.fixture(scope="session")
def observed(catalog: YamlCatalogRepository) -> DatasetDefinition:
    dataset = catalog.get("climate_observed_test")
    assert dataset is not None
    return dataset


# --------------------------------------------------------------------------- #
# Placeholders embedded in file names
# --------------------------------------------------------------------------- #
def test_country_placeholder_is_rendered_in_file_name(phenology: DatasetDefinition) -> None:
    query = make_query(phenology, {"country": "ken"})
    [path] = DuckDBTabularReader.build_relative_paths(query)
    assert path.endswith("/level=adm1/KEN_seasonal-phenology.parquet")
    query = make_query(phenology, {"country": "eth", "variant": "plus_rain"})
    assert DuckDBTabularReader.build_relative_paths(query)[0].endswith(
        "/ETH_seasonal-phenology_plus-rain.parquet"
    )
    query = make_query(phenology, {"country": "ken,eth"})
    assert len(DuckDBTabularReader.build_relative_paths(query)) == 2


def test_auto_fields_return_every_source_column(
    reader: DuckDBTabularReader, phenology: DatasetDefinition
) -> None:
    standard = reader.query(make_query(phenology, {"country": "KEN"}))
    assert standard.columns == ["admin1_name", "year", "season", "sos", "eos"]
    assert standard.total_count == 8
    assert standard.records[0]["admin1_name"] == "Nairobi"
    plus_rain = reader.query(make_query(phenology, {"country": "KEN", "variant": "plus_rain"}))
    assert "rain_total" in plus_rain.columns
    assert {row["season"] for row in plus_rain.records} == {"1", "2"}


def test_column_filter_on_auto_dataset(
    reader: DuckDBTabularReader, phenology: DatasetDefinition
) -> None:
    result = reader.query(make_query(phenology, {"country": "ETH", "season": "2"}))
    assert result.total_count == 2
    assert all(row["season"] == "2" and row["sos"] == 240 for row in result.records)


def test_partition_index_reads_values_from_file_names(
    local_storage: LocalStorage, phenology: DatasetDefinition
) -> None:
    index = StoragePartitionIndex(local_storage, ttl_seconds=0)
    assert index.available_values(phenology, "country") == ["ETH", "KEN"]
    combinations = index.available_combinations(phenology)
    kenya = next(item for item in combinations if item["country"] == "KEN")
    assert set(kenya["file_patterns"].split(",")) == {"standard", "plus_rain"}


def test_pattern_to_regex() -> None:
    regex = pattern_to_regex("{country}_seasonal-phenology.parquet")
    assert regex.fullmatch("KEN_seasonal-phenology.parquet").group("country") == "KEN"
    assert regex.fullmatch("KEN_seasonal-phenology_plus-rain.parquet") is None
    assert pattern_to_regex("int*.parquet").fullmatch("int=multi-hazard.parquet")
    assert pattern_to_regex("variable=adm?_obs.parquet").fullmatch("variable=adm1_obs.parquet")


# --------------------------------------------------------------------------- #
# Single-file datasets with the level encoded in the file name
# --------------------------------------------------------------------------- #
def test_single_file_dataset_selects_file_by_filter(
    reader: DuckDBTabularReader, observed: DatasetDefinition
) -> None:
    national = reader.query(make_query(observed, {"country": "KEN"}))
    assert national.source_files == 1
    assert all(row["admin1_name"] is None for row in national.records)
    regional = reader.query(
        make_query(observed, {"country": "KEN", "admin_level": "1", "variable": "prec"})
    )
    assert {row["admin1_name"] for row in regional.records} == {"Nairobi", "Nakuru"}
    assert {row["variable"] for row in regional.records} == {"prec"}
    assert isinstance(regional.records[0]["year"], int)


def test_single_file_dataset_partition_filter(
    reader: DuckDBTabularReader, observed: DatasetDefinition
) -> None:
    query = make_query(observed, {"country": "KEN", "aggregation": "admin-periods"})
    assert (
        "processing=admin-periods/variable=adm0_obs.parquet"
        in (DuckDBTabularReader.build_relative_paths(query)[0])
    )
    assert reader.query(query).total_count == 4


# --------------------------------------------------------------------------- #
# Through the HTTP layer
# --------------------------------------------------------------------------- #
def test_records_endpoint_serves_any_dataset(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/phenology_seasonal_test/records",
        params={"country": "KEN", "variant": "plus_rain", "limit": "3"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["total_count"] == 8
    assert "rain_total" in payload["data"][0]

    response = client.get(
        "/api/v1/datasets/climate_observed_test/records",
        params={"country": "KEN", "admin_level": "1", "format": "csv"},
    )
    assert response.status_code == 200
    assert response.text.splitlines()[0] == "iso3,admin1_name,year,month,variable,value,unit"


def test_dataset_detail_without_explicit_fields(client: TestClient) -> None:
    payload = client.get("/api/v1/datasets/phenology_seasonal_test").json()
    assert payload["fields"] == []
    assert payload["domain"] == "phenology"
    assert payload["available_partition_values"]["country"] == ["ETH", "KEN"]


def test_two_countries_from_two_files(
    reader: DuckDBTabularReader, phenology: DatasetDefinition
) -> None:
    result = reader.query(make_query(phenology, {"country": ["KEN", "ETH"]}))
    assert result.source_files == 2
    assert result.total_count == 12
    assert result.applied_filters["country"] == ["KEN", "ETH"]
