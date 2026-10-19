"""Integration tests of HU-001 through the generic records endpoint (AC-01..AC-11)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi.testclient import TestClient

URL = "/api/v1/datasets/hazard_exposure/records"


def test_ac01_successful_query_returns_expected_fields(client: TestClient) -> None:
    response = client.get(
        URL,
        params={"country": "KEN", "period": "historic", "crop": "maize", "severity": "moderate"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["dataset_id"] == "hazard_exposure"
    assert payload["metadata"]["filters"]["crop"] == "maize"
    assert payload["data"]
    record = payload["data"][0]
    for field in (
        "iso3",
        "admin0_name",
        "admin1_name",
        "admin2_name",
        "timeframe",
        "crop",
        "severity",
        "exposure_var",
        "exposure_unit",
        "value",
        "season_type",
    ):
        assert field in record
    assert record["iso3"] == "KEN"


def test_ac02_country_and_period_are_enforced(client: TestClient) -> None:
    response = client.get(URL, params={"country": "ETH", "period": "historic"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data
    assert {row["iso3"] for row in data} == {"ETH"}
    assert {row["timeframe"] for row in data} == {"historic"}


def test_ac03_crop_filter_is_enforced(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic", "crop": "maize"})
    assert {row["crop"] for row in response.json()["data"]} == {"maize"}


def test_ac04_severity_filter_is_enforced(client: TestClient) -> None:
    response = client.get(
        URL, params={"country": "KEN", "period": "historic", "severity": "severe"}
    )
    assert {row["severity"] for row in response.json()["data"]} == {"severe"}


def test_ac05_json_is_the_default_format(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic"})
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"metadata", "data"}


def test_ac06_csv_has_same_records_and_fields(client: TestClient) -> None:
    params = {
        "country": "KEN",
        "period": "historic",
        "crop": "bean",
        "admin_level": "1",
        "limit": "200",
    }
    json_payload = client.get(URL, params=params).json()
    csv_response = client.get(URL, params={**params, "format": "csv"})
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == len(json_payload["data"])
    assert list(rows[0].keys()) == list(json_payload["data"][0].keys())
    assert [r["admin1_name"] for r in rows] == [r["admin1_name"] for r in json_payload["data"]]


def test_ac07_unsupported_crop_returns_validation_error(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic", "crop": "mango"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "invalid_filter_value"
    assert payload["parameter"] == "crop"
    assert "maize" in payload["allowed_values"]


def test_ac08_unsupported_filter_returns_validation_error(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic", "colour": "red"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "unsupported_filter"
    assert payload["parameter"] == "colour"
    assert "crop" in payload["allowed_values"]


def test_ac09_no_matching_records_is_a_successful_empty_result(client: TestClient) -> None:
    response = client.get(
        URL, params={"country": "KEN", "period": "historic", "severity": "extreme"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data"] == []
    assert payload["metadata"]["total_count"] == 0


def test_ac11_unknown_dataset_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/not_a_dataset/records", params={"country": "KEN", "period": "historic"}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "dataset_not_found"


def test_missing_required_parameter_returns_422(client: TestClient) -> None:
    response = client.get(URL, params={"period": "historic"})
    assert response.status_code == 422
    assert response.json()["parameter"] == "country"


def test_admin_level_zero_returns_country_level_records(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic", "admin_level": "0"})
    data = response.json()["data"]
    assert data
    assert all(r["admin1_name"] is None and r["admin2_name"] is None for r in data)


def test_pagination_metadata(client: TestClient) -> None:
    response = client.get(
        URL, params={"country": "KEN", "period": "historic", "limit": "3", "offset": "3"}
    )
    metadata = response.json()["metadata"]
    assert metadata["limit"] == 3
    assert metadata["offset"] == 3
    assert metadata["returned_count"] == 3
    assert metadata["has_more"] is True


def test_limit_above_maximum_is_rejected(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "period": "historic", "limit": "100000"})
    assert response.status_code == 422
    assert response.json()["parameter"] == "limit"


def test_nex_source_query(client: TestClient) -> None:
    response = client.get(
        URL,
        params={
            "country": "eth",
            "period": "2041-2060",
            "scenario": "ssp245",
            "source": "nex-gddp-cmip6",
            "crop": "maize",
            "admin_level": "2",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data
    assert data[0]["gaul2_code"] is not None
    assert data[0]["season_type"] == "jagermeyr"


def test_filters_json_parameter(client: TestClient) -> None:
    response = client.get(
        URL, params={"filters": '{"country": "ken", "period": "historic", "crop": "maize"}'}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["filters"]["country"] == "KEN"
    assert {row["crop"] for row in payload["data"]} == {"maize"}


def test_filters_json_numbers_and_mixing_with_query_parameters(client: TestClient) -> None:
    response = client.get(
        "/api/v1/datasets/climate_observed_admin/records",
        params={"country": "KEN", "filters": '{"admin_level": 1, "year": 2020, "month": 2}'},
    )
    assert response.status_code == 200
    assert all(row["month"] == 2 for row in response.json()["data"])


def test_filters_json_invalid(client: TestClient) -> None:
    response = client.get(URL, params={"filters": "{country: KEN"})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_filters_json"
    response = client.get(URL, params={"filters": '["KEN"]'})
    assert response.json()["code"] == "invalid_filters_json"
    response = client.get(URL, params={"filters": '{"country": {"a": "KEN"}}'})
    assert response.json()["code"] == "invalid_filters_json"
    response = client.get(URL, params={"filters": '{"country": [["KEN"]]}'})
    assert response.json()["code"] == "invalid_filters_json"


def test_filters_json_conflict(client: TestClient) -> None:
    response = client.get(URL, params={"country": "KEN", "filters": '{"country": "ETH"}'})
    assert response.status_code == 422
    assert response.json()["code"] == "conflicting_filter"
    assert response.json()["parameter"] == "country"
    # Agreeing duplicates are accepted.
    response = client.get(
        URL, params={"country": "KEN", "filters": '{"country": "KEN", "period": "historic"}'}
    )
    assert response.status_code == 200


def test_filters_json_still_validated_against_catalog(client: TestClient) -> None:
    response = client.get(
        URL, params={"filters": '{"country": "KEN", "period": "historic", "colour": "red"}'}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_filter"


def test_multiple_values_in_every_form(client: TestClient) -> None:
    expected = {"maize", "bean"}
    base = {"country": "KEN", "period": "historic", "severity": "moderate"}
    repeated = client.get(URL, params=[*base.items(), ("crop", "maize"), ("crop", "bean")])
    comma = client.get(URL, params={**base, "crop": "maize,bean"})
    as_json = client.get(
        URL,
        params={
            "filters": (
                '{"country":"KEN","period":"historic","severity":"moderate",'
                '"crop":["maize","bean"]}'
            )
        },
    )
    for response in (repeated, comma, as_json):
        assert response.status_code == 200
        payload = response.json()
        assert {row["crop"] for row in payload["data"]} == expected
        assert payload["metadata"]["filters"]["crop"] == ["maize", "bean"]
    assert repeated.json()["metadata"]["total_count"] == comma.json()["metadata"]["total_count"]


def test_invalid_value_inside_list_is_reported(client: TestClient) -> None:
    response = client.get(
        URL, params={"country": "KEN", "period": "historic", "crop": "maize,mango"}
    )
    assert response.status_code == 422
    assert response.json()["parameter"] == "crop"


def test_list_conflict_between_json_and_query(client: TestClient) -> None:
    response = client.get(
        URL,
        params={
            "country": "KEN",
            "period": "historic",
            "crop": "maize",
            "filters": '{"crop":["maize","bean"]}',
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "conflicting_filter"


def test_corrupt_source_file_yields_partial_response(
    client: TestClient, storage_root: Path
) -> None:
    leaf = (
        storage_root
        / "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure"
        / "variable=vop_intld15/period=annual/model=historic/severity=extreme"
    )
    leaf.mkdir(parents=True, exist_ok=True)
    corrupt = leaf / "interaction.parquet"
    corrupt.write_bytes(b"not a parquet file")
    try:
        response = client.get(
            "/api/v1/datasets/hazard_exposure/records",
            params={"country": "KEN", "period": "historic"},
        )
        assert response.status_code == 200
        metadata = response.json()["metadata"]
        assert metadata["partial"] is True
        assert metadata["skipped_files"] == [
            "source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure/"
            "variable=vop_intld15/period=annual/model=historic/severity=extreme/interaction.parquet"
        ]
        assert metadata["source_files"] == 2
        assert metadata["total_count"] > 0
    finally:
        corrupt.unlink()
        leaf.rmdir()


def test_complete_response_is_not_partial(client: TestClient) -> None:
    metadata = client.get(
        "/api/v1/datasets/hazard_exposure/records", params={"country": "KEN", "period": "historic"}
    ).json()["metadata"]
    assert metadata["partial"] is False
    assert metadata["skipped_files"] == []
