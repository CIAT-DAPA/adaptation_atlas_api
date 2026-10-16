"""Generic records endpoint: serves every tabular dataset registered in the catalog."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from adaptation_atlas_api.adapters.registry import resolve_tabular_exporter
from adaptation_atlas_api.api.deps import get_tabular_service
from adaptation_atlas_api.api.schemas.common import ERROR_RESPONSES
from adaptation_atlas_api.api.schemas.records import RecordsResponse
from adaptation_atlas_api.domain.models.query import OutputFormat
from adaptation_atlas_api.domain.services.tabular_dataset import TabularDatasetService

router = APIRouter(prefix="/datasets", tags=["Records"])

DESCRIPTION = """
Retrieve records from any tabular dataset published in the catalog.

**Filters are dataset specific.** Call `GET /datasets/{dataset_id}` first: it lists the
query parameters the dataset accepts, which ones are required, their valid values and the
fields of every record. Then pass those parameters as query parameters of this endpoint,
for example:

```
GET /datasets/hazard_exposure/records?country=KEN&period=historic&crop=maize&admin_level=1
GET /datasets/climate_observed_admin/records?country=ETH&admin_level=1&variable=prec
```

Behaviour shared by every dataset:

* Parameters not registered for the dataset, missing required parameters and values
  outside the controlled vocabularies return **422** naming the parameter and its valid
  values; no query is executed.
* Valid filters with no matching record return **200** with an empty `data` list.
* `format=csv` streams the same logical records and fields as the JSON response.
* Results are paginated with `limit` and `offset`; `metadata.total_count` reports the
  total number of matching records.
"""


@router.get(
    "/{dataset_id}/records",
    response_model=RecordsResponse,
    responses={
        200: {
            "description": "Matching records in JSON, or a CSV stream when `format=csv`.",
            "content": {
                "application/json": {},
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **ERROR_RESPONSES,
    },
    summary="Retrieve records from a dataset",
    description=DESCRIPTION,
    openapi_extra={
        "parameters": [
            {
                "in": "query",
                "name": "<dataset filters>",
                "required": False,
                "description": (
                    "Any filter listed by `GET /datasets/{dataset_id}` (e.g. `country`, "
                    "`period`, `crop`, `admin_level`). Required filters depend on the dataset."
                ),
                "schema": {"type": "string"},
                "allowEmptyValue": True,
            }
        ]
    },
)
def get_records(
    request: Request,
    dataset_id: str = Path(
        ..., description="Public dataset identifier.", examples=["hazard_exposure"]
    ),
    format: OutputFormat = Query(default=OutputFormat.JSON, description="Response format."),
    limit: int | None = Query(
        default=None, ge=1, description="Page size (defaults to the configured page size)."
    ),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
    service: TabularDatasetService = Depends(get_tabular_service),
) -> RecordsResponse | StreamingResponse:
    """Serve records of a tabular dataset.

    Every query parameter actually received (except the control parameters) is handed to
    the service, which validates it against the dataset definition in the catalog.

    Args:
        request: Incoming request, used to collect every query parameter.
        dataset_id: Public dataset identifier.
        format: Response format.
        limit: Page size.
        offset: Records to skip.
        service: Generic tabular service.

    Returns:
        The JSON payload, or a streaming CSV response.
    """
    raw_filters: dict[str, str | None] = dict(request.query_params)
    result = service.query(
        dataset_id,
        raw_filters,
        output_format=format.value,
        limit=limit,
        offset=offset,
    )

    exporter = resolve_tabular_exporter(format)
    # JSON is rendered by the response model; other formats are streamed by an exporter.
    if exporter is None:
        return RecordsResponse.from_result(result)

    suffix = "_".join(value for value in result.applied_filters.values() if value)[:80]
    file_name = f"{dataset_id}_{suffix or 'all'}.{exporter.file_extension}"
    return StreamingResponse(
        exporter.export(result),
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
