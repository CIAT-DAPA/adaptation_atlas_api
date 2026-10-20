"""Adapter tests for the CSV exporter."""

from __future__ import annotations

import csv
import io

from adaptation_atlas_api.shared.domain.results import TabularResult
from adaptation_atlas_api.shared.infrastructure.csv_exporter import CsvExporter


def test_csv_contains_header_and_same_records() -> None:
    result = TabularResult(
        dataset_id="x",
        columns=["iso3", "admin1_name", "value"],
        records=[
            {"iso3": "KEN", "admin1_name": None, "value": 1.5},
            {"iso3": "KEN", "admin1_name": "Nairobi, West", "value": 2.0},
        ],
        total_count=2,
        limit=10,
    )
    text = "".join(CsvExporter(chunk_rows=1).export(result))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [r["iso3"] for r in rows] == ["KEN", "KEN"]
    assert rows[0]["admin1_name"] == ""
    assert rows[1]["admin1_name"] == "Nairobi, West"
    assert rows[1]["value"] == "2.0"


def test_empty_result_still_has_header() -> None:
    result = TabularResult.empty("x", ["iso3", "value"], limit=10)
    text = "".join(CsvExporter().export(result))
    assert text.strip() == "iso3,value"
