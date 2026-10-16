"""Integration tests of HU-001 through the HTTP layer (acceptance criteria AC-01..AC-11)."""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

URL = "/api/v1/hazard-exposure/hazard_exposure"


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
        "/api/v1/hazard-exposure/not_a_dataset", params={"country": "KEN", "period": "historic"}
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
