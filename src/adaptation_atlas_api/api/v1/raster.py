"""Generic raster endpoint: serves every raster dataset registered in the catalog.

Observed climate series (HU-004) and climate change scenarios (HU-003) are served by
the same endpoint; the catalog entry of each dataset decides which filters, variables,
units and aggregations are available.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, Path, Response
from fastapi.responses import StreamingResponse

from adaptation_atlas_api.adapters.registry import resolve_raster_exporter, resolve_tabular_exporter
from adaptation_atlas_api.api.deps import get_raster_service
from adaptation_atlas_api.api.schemas.common import ERROR_RESPONSES
from adaptation_atlas_api.api.schemas.raster import RasterRequest
from adaptation_atlas_api.api.schemas.records import RecordsResponse
from adaptation_atlas_api.domain.models.query import OutputFormat
from adaptation_atlas_api.domain.models.spatial import RasterFormat, RasterResult
from adaptation_atlas_api.domain.services.point_records import point_samples_to_records
from adaptation_atlas_api.domain.services.raster_dataset import RasterDatasetService

router = APIRouter(prefix="/datasets", tags=["Raster"])

METADATA_HEADER = "X-Atlas-Metadata"

DESCRIPTION = """
Retrieve a spatial subset of any raster dataset published in the catalog, optionally
reprojected, resampled, converted to another unit and aggregated over time.

**Filters and variables are dataset specific.** Call `GET /datasets/{dataset_id}` first:
it lists the filters, the variables with their units and aggregation rules, the supported
coordinate reference systems, resolutions, resampling methods and aggregation types, and
an `example_request` ready to paste.

**Geographic selection** (exactly one):

* `geometry`: a GeoJSON Polygon/MultiPolygon in WGS84 — the rasters are clipped to it;
* `bbox`: `[min_lon, min_lat, max_lon, max_lat]` — the rasters are clipped to the box;
* `points`: `[[lon, lat], ...]` — the values at each coordinate are returned as a table.

**Temporal selection**: `start_date` and `end_date` select the layers (months, years...)
of the series. `aggregation` combines them with the rule registered for the variable
(`sum` for totals such as precipitation, `mean` for indices and temperatures):
`period` (one value), `calendar_month`, `fixed_window` (e.g. 30-day windows from a start
date), `crop_season` (planting date plus end date or duration) or `stages` (explicit
windows). Layers that only partially overlap a window contribute in proportion to their
overlapping days, and every output slice reports the fraction of its window actually
covered by data.

**Transformations**: `target_crs`, `resolution` and `resampling` reproject and resample;
`unit` converts the values with a conversion registered in the catalog (never inferred).

**Output**: area selections return a file (`geotiff`: one band per time step or window;
`netcdf`: a `(time, y, x)` variable). The processing metadata travels in the
`X-Atlas-Metadata` response header (JSON) and inside the file (GeoTIFF tags / NetCDF
attributes). Point selections return the same table as the records endpoint (`json` or
`csv`) with one row per point and time step or window.

Validation errors (**422**) name the parameter and, when known, its valid values; a
geographic selection outside the coverage returns `no_coverage`, a period without layers
`period_outside_coverage`, and a request exceeding the service limits `request_too_large`.
"""


def _file_name(result: RasterResult, extension: str) -> str:
    """Build the download file name of a product.

    Args:
        result: The raster result.
        extension: File extension without dot.

    Returns:
        A file name such as ``climate_observed_monthly_PTOT_2020-01_2020-12.tif``.
    """
    first = result.output_layers[0].start.isoformat() if result.output_layers else "all"
    last = result.output_layers[-1].end.isoformat() if result.output_layers else "all"
    return f"{result.dataset_id}_{result.variable}_{first}_{last}.{extension}"


def _metadata_header(result: RasterResult) -> str:
    """Render the processing metadata as a compact, header-safe JSON string.

    Args:
        result: The raster result.

    Returns:
        ASCII JSON text.
    """
    return json.dumps(result.describe(), separators=(",", ":"), ensure_ascii=True, default=str)


@router.post(
    "/{dataset_id}/raster",
    responses={
        200: {
            "description": (
                "The spatial product (GeoTIFF or NetCDF) with its metadata in the "
                "`X-Atlas-Metadata` header, or a table of point values (JSON or CSV)."
            ),
            "content": {
                "image/tiff": {"schema": {"type": "string", "format": "binary"}},
                "application/x-netcdf": {"schema": {"type": "string", "format": "binary"}},
                "application/json": {"schema": RecordsResponse.model_json_schema()},
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **ERROR_RESPONSES,
    },
    summary="Retrieve a spatial subset of a raster dataset",
    description=DESCRIPTION,
)
def post_raster(
    dataset_id: str = Path(
        ..., description="Public raster dataset identifier.", examples=["climate_observed_monthly"]
    ),
    body: RasterRequest = Body(...),
    service: RasterDatasetService = Depends(get_raster_service),
) -> Response:
    """Serve a raster product or point values for a raster dataset.

    Args:
        dataset_id: Public dataset identifier.
        body: Validated request body.
        service: Generic raster service.

    Returns:
        A file response (area selections) or a JSON/CSV response (point selections).
    """
    request = body.model_dump(exclude_none=True)
    # The aggregation sub-model is handed to the domain as a plain mapping.
    if body.aggregation is not None:
        request["aggregation"] = body.aggregation.model_dump(exclude_none=True)
    result = service.query(dataset_id, request)
    output_format = RasterFormat(request.get("format", "geotiff"))

    # Area products are exported to a spatial file with the metadata in a header.
    if result.is_area:
        exporter = resolve_raster_exporter(output_format)
        content = exporter.export(result)
        return Response(
            content=content,
            media_type=exporter.media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{_file_name(result, exporter.file_extension)}"'
                ),
                METADATA_HEADER: _metadata_header(result),
            },
        )

    # Point values reuse the tabular response and exporters.
    records = point_samples_to_records(result)
    headers = {METADATA_HEADER: _metadata_header(result)}
    # JSON is rendered by the records response model.
    if output_format is RasterFormat.JSON:
        payload = RecordsResponse.from_result(records)
        return Response(
            content=payload.model_dump_json(), media_type="application/json", headers=headers
        )
    tabular_exporter = resolve_tabular_exporter(OutputFormat.CSV)
    assert tabular_exporter is not None  # noqa: S101 - CSV always has an exporter
    headers["Content-Disposition"] = (
        f'attachment; filename="{_file_name(result, tabular_exporter.file_extension)}"'
    )
    return StreamingResponse(
        tabular_exporter.export(records), media_type=tabular_exporter.media_type, headers=headers
    )
