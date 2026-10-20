"""End-to-end tests of POST /datasets/{dataset_id}/raster and the catalog type filter."""

from __future__ import annotations

import io
import json

import rasterio
from fastapi.testclient import TestClient

URL = "/api/v1/datasets/climate_observed_monthly/raster"
BOX = [32.0, -2.0, 34.0, 0.0]


def test_geotiff_response_with_metadata_header(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            "bbox": BOX,
            "aggregation": {"type": "period"},
            "unit": "in",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/tiff"
    assert response.headers["content-disposition"].endswith(
        'climate_observed_monthly_PTOT_2020-01-01_2020-03-31.tif"'
    )
    metadata = json.loads(response.headers["X-Atlas-Metadata"])
    assert metadata["unit"] == "in" and metadata["aggregation"] == "period"
    assert [step["step"] for step in metadata["processing"]] == [
        "source",
        "clip",
        "unit_conversion",
        "temporal_aggregation",
    ]
    assert metadata["layers"][0]["coverage"] == 1.0
    with rasterio.open(io.BytesIO(response.content)) as dataset:
        assert dataset.count == 1
        assert abs(dataset.read(1)[0, 0] - (1 + 2 + 3) * 0.0393700787) < 1e-4


def test_netcdf_response(client: TestClient) -> None:
    response = client.post(
        URL,
        json={
            "filters": {"variable": "PTOT"},
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "bbox": BOX,
            "format": "netcdf",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-netcdf"
    assert response.content[:4] in (b"\x89HDF", b"CDF\x01", b"CDF\x02")


def test_points_as_json_and_csv(client: TestClient) -> None:
    body = {
        "filters": {"variable": "PTOT"},
        "start_date": "2020-01-01",
        "end_date": "2020-02-29",
        "points": [[33.0, -1.0]],
        "aggregation": {"type": "calendar_month"},
        "format": "json",
    }
    response = client.post(URL, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["total_count"] == 2
    assert payload["data"][0]["period"] == "2020-01" and payload["data"][0]["value"] == 1.0
    assert json.loads(response.headers["X-Atlas-Metadata"])["points"] == [[33.0, -1.0]]
    csv_response = client.post(URL, json={**body, "format": "csv"})
    assert csv_response.status_code == 200
    lines = csv_response.text.splitlines()
    assert lines[0].startswith(
        "point,longitude,latitude,period,start,end,coverage,variable,value,unit"
    )
    assert len(lines) == 3


def test_validation_errors_are_uniform(client: TestClient) -> None:
    base = {"filters": {"variable": "PTOT"}, "start_date": "2020-01-01", "end_date": "2020-01-31"}
    cases = [
        ({**base}, "invalid_geometry", "geometry"),
        ({**base, "bbox": [34, 0, 32, -2]}, "invalid_bbox", "bbox"),
        ({**base, "points": [[500, 0]]}, "invalid_coordinates", "points"),
        ({**base, "bbox": BOX, "unit": "gallons"}, "unsupported_unit", "unit"),
        (
            {**base, "bbox": BOX, "aggregation": {"type": "weekly"}},
            "unsupported_aggregation",
            "aggregation.type",
        ),
        ({**base, "bbox": BOX, "resampling": "lanczos"}, "unsupported_resampling", "resampling"),
        ({**base, "bbox": BOX, "start_date": "2020-02-01"}, "invalid_date_range", "start_date"),
        (
            {"filters": {}, "bbox": BOX, **{k: base[k] for k in ("start_date", "end_date")}},
            "missing_required_filter",
            "variable",
        ),
        (
            {**base, "filters": {"variable": "RAIN"}, "bbox": BOX},
            "invalid_filter_value",
            "variable",
        ),
        ({**base, "bbox": [-10, -10, -5, -5]}, "no_coverage", "geometry"),
        ({**base, "bbox": BOX, "format": "csv"}, "unsupported_format", "format"),
        (
            {**base, "start_date": "1990-01-01", "end_date": "1990-12-31", "bbox": BOX},
            "period_outside_coverage",
            "start_date",
        ),
    ]
    for body, code, parameter in cases:
        response = client.post(URL, json=body)
        assert response.status_code == 422, (body, response.text)
        payload = response.json()
        assert payload["code"] == code, (body, payload)
        assert payload["parameter"] == parameter, (body, payload)


def test_unknown_combination_and_dataset(client: TestClient) -> None:
    response = client.post(
        "/api/v1/datasets/climate_hazard_indices/raster",
        json={
            "filters": {
                "scenario": "ssp585",
                "period": "2081-2100",
                "model": "EC-Earth3",
                "variable": "ndd",
            },
            "bbox": BOX,
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "unsupported_combination"
    assert client.post("/api/v1/datasets/nope/raster", json={"bbox": BOX}).status_code == 404


def test_dataset_type_is_enforced_on_both_endpoints(client: TestClient) -> None:
    wrong = client.post("/api/v1/datasets/hazard_exposure/raster", json={"bbox": BOX})
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "wrong_dataset_type"
    assert "GET /datasets/hazard_exposure/records" in wrong.json()["message"]
    wrong = client.get("/api/v1/datasets/climate_observed_monthly/records")
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "wrong_dataset_type"
    assert "POST /datasets/climate_observed_monthly/raster" in wrong.json()["message"]


def test_catalog_lists_and_describes_raster_datasets(client: TestClient) -> None:
    rasters = client.get("/api/v1/datasets", params={"type": "raster"}).json()
    assert {item["id"] for item in rasters} == {
        "climate_observed_monthly",
        "climate_hazard_indices",
    }
    assert all(item["type"] == "raster" for item in rasters)
    tabular = client.get("/api/v1/datasets", params={"type": "tabular"}).json()
    assert all(item["type"] == "tabular" for item in tabular) and len(tabular) == 19
    assert client.get("/api/v1/datasets", params={"type": "vector"}).status_code == 422

    detail = client.get("/api/v1/datasets/climate_observed_monthly").json()
    assert detail["type"] == "raster" and detail["fields"] == []
    raster = detail["raster"]
    assert raster["temporal_resolution"] == "monthly" and raster["crs"] == "EPSG:4326"
    ptot = next(item for item in raster["variables"] if item["name"] == "PTOT")
    assert ptot["available_units"] == ["mm", "in", "cm"] and ptot["aggregation"] == "sum"
    assert detail["example_query"] == "/api/v1/datasets/climate_observed_monthly/raster"
    assert detail["available_partition_values"]["variable"] == ["PTOT", "SPEI-03"]
    # The example request must be a valid request against the synthetic data.
    example = detail["example_request"]
    assert example["filters"] == {"variable": "PTOT"}
    example["bbox"] = BOX
    assert client.post(detail["example_query"], json=example).status_code == 200
    scenarios = client.get("/api/v1/datasets/climate_hazard_indices").json()
    assert scenarios["raster"]["time_range_required"] is False
    assert "example_request" in scenarios and "start_date" not in scenarios["example_request"]
    tabular_detail = client.get("/api/v1/datasets/hazard_exposure").json()
    assert tabular_detail["raster"] is None and tabular_detail["example_request"] == {}


def test_openapi_documents_raster_endpoint(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    path = schema["paths"]["/api/v1/datasets/{dataset_id}/raster"]["post"]
    assert "Raster" in path["tags"]
    assert "image/tiff" in path["responses"]["200"]["content"]
    assert "422" in path["responses"]
