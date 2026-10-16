"""Adapter tests for the DuckDB reader against the synthetic Parquet tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from adaptation_atlas_api.adapters.storage.local_storage import LocalStorage
from adaptation_atlas_api.adapters.tabular.duckdb_reader import DuckDBTabularReader
from adaptation_atlas_api.domain.exceptions import SourceUnavailableError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.models.query import OutputFormat, TabularQuery
from adaptation_atlas_api.domain.validation.filters import validate_filters

CONTROL = {"format", "limit", "offset"}


def make_query(
    dataset: DatasetDefinition, filters: dict[str, str], limit: int = 1000, offset: int = 0
) -> TabularQuery:
    validated = validate_filters(dataset, filters, CONTROL)
    return TabularQuery(
        dataset=dataset,
        filters=validated,
        limit=limit,
        offset=offset,
        output_format=OutputFormat.JSON,
    )


def test_relative_path_uses_values_or_wildcards(hazard_dataset: DatasetDefinition) -> None:
    query = make_query(
        hazard_dataset, {"country": "KEN", "period": "historic", "severity": "severe"}
    )
    path = DuckDBTabularReader.build_relative_path(query)
    assert path == (
        "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure/"
        "variable=*/period=*/model=*/severity=severe/int*.parquet"
    )


def test_country_and_period_are_enforced(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(make_query(hazard_dataset, {"country": "KEN", "period": "historic"}))
    assert result.total_count > 0
    assert {row["iso3"] for row in result.records} == {"KEN"}
    assert {row["timeframe"] for row in result.records} == {"historic"}
    # Both severities of the historic model are read because severity was not filtered.
    assert {row["severity"] for row in result.records} == {"moderate", "severe"}
    assert result.source_files == 2


def test_crop_and_severity_filters(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(
            hazard_dataset,
            {"country": "KEN", "period": "historic", "crop": "maize", "severity": "severe"},
        )
    )
    assert result.records
    assert {row["crop"] for row in result.records} == {"maize"}
    assert {row["severity"] for row in result.records} == {"severe"}
    assert result.source_files == 1


def test_admin_level_zero_returns_country_rows_only(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "admin_level": "0"})
    )
    assert result.records
    assert all(row["admin1_name"] is None and row["admin2_name"] is None for row in result.records)


def test_admin_level_two_returns_district_rows(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "admin_level": "2"})
    )
    assert result.records
    assert all(row["admin2_name"] is not None for row in result.records)


def test_solo_file_pattern_selects_solo_file(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "hazard_type": "solo"})
    )
    assert result.source_files == 1
    assert result.total_count > 0


def test_missing_columns_are_null_and_partitions_exposed(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "admin_level": "0"})
    )
    row = result.records[0]
    assert set(result.columns) == set(row)
    assert row["gaul0_code"] is None
    assert row["season_type"] == "annual"
    assert row["source"] == "atlas_cmip6"


def test_gaul_codes_are_integers_for_nex_source(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(
            hazard_dataset,
            {
                "country": "ETH",
                "period": "2041-2060",
                "scenario": "ssp245",
                "source": "nex-gddp-cmip6",
                "admin_level": "2",
            },
        )
    )
    assert result.records
    row = result.records[0]
    assert isinstance(row["gaul2_code"], int)
    assert row["exposure_unit"] == "nominal-usd-2021"
    assert {r["scenario"] for r in result.records} == {"ssp245"}


def test_no_matching_partition_returns_empty_result(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    result = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "severity": "extreme"})
    )
    assert result.records == []
    assert result.total_count == 0
    assert result.source_files == 0


def test_no_matching_rows_returns_empty_result(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    # The historic atlas file only contains the historic timeframe.
    result = reader.query(make_query(hazard_dataset, {"country": "KEN", "period": "2081-2100"}))
    assert result.records == []
    assert result.total_count == 0
    assert result.source_files == 2


def test_pagination_is_stable_and_counts_total(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    filters = {"country": "KEN", "period": "historic", "severity": "moderate"}
    full = reader.query(make_query(hazard_dataset, filters))
    first = reader.query(make_query(hazard_dataset, filters, limit=5, offset=0))
    second = reader.query(make_query(hazard_dataset, filters, limit=5, offset=5))
    assert first.total_count == full.total_count == second.total_count
    assert first.has_more is True
    assert first.records == full.records[:5]
    assert second.records == full.records[5:10]
    beyond = reader.query(make_query(hazard_dataset, filters, limit=5, offset=10_000))
    assert beyond.records == []
    assert beyond.total_count == full.total_count


def test_unreadable_source_raises_controlled_error(
    tmp_path: Path, hazard_dataset: DatasetDefinition
) -> None:
    leaf = (
        tmp_path
        / "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure"
        "/variable=vop_intld15/period=annual/model=historic/severity=moderate"
    )
    leaf.mkdir(parents=True)
    (leaf / "interaction.parquet").write_bytes(b"this is not a parquet file")
    broken_reader = DuckDBTabularReader(LocalStorage(tmp_path), query_timeout_seconds=10)
    with pytest.raises(SourceUnavailableError) as error:
        broken_reader.query(make_query(hazard_dataset, {"country": "KEN", "period": "historic"}))
    assert str(tmp_path) not in error.value.message
