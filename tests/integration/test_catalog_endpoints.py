"""Integration tests of the health and catalog endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["datasets"] == 1


def test_list_datasets(client: TestClient) -> None:
    response = client.get("/api/v1/datasets")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["hazard_exposure"]


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
    assert response.json()["allowed_values"] == ["hazard_exposure"]


def test_openapi_documents_hazard_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/hazard-exposure/{dataset_id}"]["get"]
    parameter_names = {item["name"] for item in path["parameters"]}
    assert {
        "country",
        "period",
        "crop",
        "severity",
        "admin_level",
        "format",
        "limit",
    } <= parameter_names
    assert "422" in path["responses"]
