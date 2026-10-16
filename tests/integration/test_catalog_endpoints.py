"""Integration tests of the health and catalog endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["datasets"] == 19


def test_list_datasets(client: TestClient) -> None:
    response = client.get("/api/v1/datasets")
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert "hazard_exposure" in ids
    assert "phenology_seasonal_test" in ids


def test_list_datasets_filtered_by_domain(client: TestClient) -> None:
    response = client.get("/api/v1/datasets", params={"domain": "hazard_exposure"})
    assert [item["id"] for item in response.json()] == ["hazard_exposure"]
    assert response.json()[0]["domain"] == "hazard_exposure"
    assert client.get("/api/v1/datasets", params={"domain": "nope"}).json() == []


def test_dataset_detail_exposes_filters_fields_and_availability(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/hazard_exposure")
    assert response.status_code == 200
    payload = response.json()
    filter_names = [item["name"] for item in payload["filters"]]
    assert filter_names[:2] == ["country", "period"]
    assert all(item["required"] for item in payload["filters"][:2])
    assert "value" in [item["name"] for item in payload["fields"]]
    assert payload["available_partition_values"]["severity"] == ["moderate", "severe"]
    assert len(payload["available_combinations"]) == 3
    assert "source_metadata" in payload["metadata"]


def test_unknown_dataset_detail_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/datasets/nope")
    assert response.status_code == 404
    assert "hazard_exposure" in response.json()["allowed_values"]


def test_openapi_documents_records_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/datasets/{dataset_id}/records"]["get"]
    parameter_names = {item["name"] for item in path["parameters"]}
    assert {"dataset_id", "filters", "format", "limit", "offset"} == parameter_names
    assert "422" in path["responses"]
    assert "/api/v1/hazard-exposure/{dataset_id}" not in schema["paths"]


def test_root_redirects_to_docs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"


def test_dataset_detail_includes_example_query(client: TestClient) -> None:
    payload = client.get("/api/v1/datasets/hazard_exposure").json()
    example = payload["example_query"]
    assert example == "/api/v1/datasets/hazard_exposure/records?country=KEN&period=historic"
    # The example must be a valid query against the API itself.
    assert client.get(example).status_code == 200
    assert payload["example_filters"] == {"country": "KEN", "period": "historic"}
    change = client.get("/api/v1/datasets/climate_hazard_change").json()
    assert change["example_query"].endswith("records?country=KEN&indicator=haz_freq_ensemble")
    assert client.get(change["example_query"]).status_code == 200
