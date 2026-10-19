"""Adapter tests for the DuckDB reader against the synthetic Parquet tree."""

from __future__ import annotations

from pathlib import Path

import duckdb
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
    paths = DuckDBTabularReader.build_relative_paths(query)
    assert paths == [
        "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure/"
        "variable=*/period=*/model=*/severity=severe/int*.parquet"
    ]


def test_multiple_values_expand_to_several_paths(hazard_dataset: DatasetDefinition) -> None:
    query = make_query(
        hazard_dataset,
        {
            "country": "KEN",
            "period": "historic",
            "severity": "moderate,severe",
            "hazard_type": "interaction,solo",
        },
    )
    paths = DuckDBTabularReader.build_relative_paths(query)
    assert len(paths) == 4
    assert paths[0].endswith("severity=moderate/int*.parquet")
    assert paths[1].endswith("severity=severe/int*.parquet")
    assert paths[2].endswith("severity=moderate/solo.parquet")


def test_multiple_values_are_combined_with_or(
    reader: DuckDBTabularReader, hazard_dataset: DatasetDefinition
) -> None:
    both = reader.query(
        make_query(
            hazard_dataset,
            {"country": "KEN", "period": "historic", "crop": "maize,bean", "severity": "moderate"},
        )
    )
    maize = reader.query(
        make_query(
            hazard_dataset,
            {"country": "KEN", "period": "historic", "crop": "maize", "severity": "moderate"},
        )
    )
    assert {row["crop"] for row in both.records} == {"maize", "bean"}
    assert both.total_count == 2 * maize.total_count
    # Several partition values read several files; several file types too.
    files = reader.query(
        make_query(
            hazard_dataset,
            {
                "country": "KEN",
                "period": "historic",
                "severity": ["moderate", "severe"],
                "hazard_type": ["interaction", "solo"],
            },
        )
    )
    assert files.source_files == 3
    assert {row["severity"] for row in files.records} == {"moderate", "severe"}
    # Several admin levels are OR-ed.
    levels = reader.query(
        make_query(hazard_dataset, {"country": "KEN", "period": "historic", "admin_level": "0,2"})
    )
    assert all(
        (row["admin1_name"] is None and row["admin2_name"] is None) or row["admin2_name"]
        for row in levels.records
    )
    assert any(row["admin2_name"] is None for row in levels.records)
    assert any(row["admin2_name"] is not None for row in levels.records)


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


HAZARD_ROOT = "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure"


def write_corrupt_file(root: Path, partition: str) -> Path:
    """Write a file that is not Parquet (simulating a truncated S3 upload)."""
    leaf = root / HAZARD_ROOT / partition
    leaf.mkdir(parents=True, exist_ok=True)
    target = leaf / "interaction.parquet"
    target.write_bytes(b"this is not a parquet file")
    return target


def test_unreadable_source_raises_controlled_error(
    tmp_path: Path, hazard_dataset: DatasetDefinition
) -> None:
    # The only matching file is corrupt: nothing can be served.
    write_corrupt_file(
        tmp_path, "variable=vop_intld15/period=annual/model=historic/severity=moderate"
    )
    broken_reader = DuckDBTabularReader(LocalStorage(tmp_path), query_timeout_seconds=10)
    with pytest.raises(SourceUnavailableError) as error:
        broken_reader.query(make_query(hazard_dataset, {"country": "KEN", "period": "historic"}))
    assert str(tmp_path) not in error.value.message


def test_corrupt_file_is_skipped_and_reported(
    storage_root: Path, hazard_dataset: DatasetDefinition
) -> None:
    # A corrupt file next to the valid synthetic ones must not fail the query.
    corrupt = write_corrupt_file(
        storage_root, "variable=vop_intld15/period=annual/model=historic/severity=extreme"
    )
    try:
        reader = DuckDBTabularReader(LocalStorage(storage_root), query_timeout_seconds=10)
        result = reader.query(make_query(hazard_dataset, {"country": "KEN", "period": "historic"}))
        assert result.total_count > 0
        assert result.source_files == 2
        assert result.is_partial is True
        assert result.skipped_files == [
            "source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure/"
            "variable=vop_intld15/period=annual/model=historic/severity=extreme/interaction.parquet"
        ]
        # The bucket/root location is never exposed.
        assert all(str(storage_root) not in item for item in result.skipped_files)
        # Pagination beyond the end still counts over the readable files only.
        beyond = reader.query(
            make_query(hazard_dataset, {"country": "KEN", "period": "historic"}, offset=10_000)
        )
        assert beyond.total_count == result.total_count
    finally:
        corrupt.unlink()
        corrupt.parent.rmdir()


def test_skip_budget_is_enforced(storage_root: Path, hazard_dataset: DatasetDefinition) -> None:
    corrupt = write_corrupt_file(
        storage_root, "variable=vop_intld15/period=annual/model=historic/severity=extreme"
    )
    try:
        strict_reader = DuckDBTabularReader(
            LocalStorage(storage_root), query_timeout_seconds=10, max_skipped_files=0
        )
        with pytest.raises(SourceUnavailableError):
            strict_reader.query(
                make_query(hazard_dataset, {"country": "KEN", "period": "historic"})
            )
    finally:
        corrupt.unlink()
        corrupt.parent.rmdir()


def test_unreadable_file_detection() -> None:
    files = ["s3://bucket/a.parquet", "s3://bucket/b.parquet"]
    corrupt = duckdb.InvalidInputException(
        "Invalid Input Error: No magic bytes found at end of file 's3://bucket/b.parquet'"
    )
    assert DuckDBTabularReader._unreadable_file(corrupt, files) == "s3://bucket/b.parquet"
    # Transport failures are not corrupt files, even when they name one.
    http = duckdb.IOException("IO Error: HTTP GET error on 's3://bucket/a.parquet' (HTTP 403)")
    assert DuckDBTabularReader._unreadable_file(http, files) is None
    # Errors that name no file in the list cannot be attributed.
    other = duckdb.InvalidInputException("Invalid Input Error: No magic bytes found 'x.parquet'")
    assert DuckDBTabularReader._unreadable_file(other, files) is None
