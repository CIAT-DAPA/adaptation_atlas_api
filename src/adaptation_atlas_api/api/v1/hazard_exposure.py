"""HU-001 endpoint: hazard exposure data for a selected territory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from adaptation_atlas_api.adapters.registry import resolve_tabular_exporter
from adaptation_atlas_api.api.deps import get_hazard_exposure_service
from adaptation_atlas_api.api.schemas.common import ERROR_RESPONSES
from adaptation_atlas_api.api.schemas.hazard_exposure import HazardExposureResponse
from adaptation_atlas_api.domain.models.query import OutputFormat
from adaptation_atlas_api.domain.services.hazard_exposure import HazardExposureService

router = APIRouter(prefix="/hazard-exposure", tags=["Hazard exposure"])

DESCRIPTION = """
Retrieve hazard exposure records (hazard frequency × exposure value) for one country and
one period, optionally narrowed by crop, hazard, severity, climate model and
administrative level.

* `country` and `period` are **required**.
* Every other parameter is optional; the valid values of each one are listed in
  `GET /datasets/{dataset_id}`.
* An unregistered parameter or a value outside the controlled vocabulary returns
  **422** naming the parameter and its valid values; no query is executed.
* Valid filters with no matching record return **200** with an empty `data` list.
* `format=csv` streams the same logical records and fields as the JSON response.
"""


@router.get(
    "/{dataset_id}",
    response_model=HazardExposureResponse,
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
    summary="Retrieve hazard exposure data for a territory",
    description=DESCRIPTION,
)
def get_hazard_exposure(
    request: Request,
    dataset_id: str = Path(
        ..., description="Public dataset identifier.", examples=["hazard_exposure"]
    ),
    country: str = Query(..., description="ISO 3166-1 alpha-3 country code.", examples=["KEN"]),
    period: str = Query(
        ...,
        description="Period analysed: `historic` or a projected window such as `2021-2040`.",
        examples=["historic"],
    ),
    source: str | None = Query(
        default=None, description="Data source (e.g. `atlas_cmip6`, `nex-gddp-cmip6`)."
    ),
    variable: str | None = Query(
        default=None, description="Exposure variable and unit (e.g. `vop_intld15`)."
    ),
    season_type: str | None = Query(
        default=None, description="Calendar used for the hazard: `annual` or `jagermeyr`."
    ),
    model: str | None = Query(
        default=None, description="Climate model or ensemble statistic (e.g. `ENSEMBLEmean`)."
    ),
    severity: str | None = Query(
        default=None, description="Hazard severity threshold: `moderate`, `severe` or `extreme`."
    ),
    hazard_type: str | None = Query(
        default=None, description="`interaction` (compound hazards) or `solo` (single hazards)."
    ),
    scenario: str | None = Query(
        default=None, description="Emissions scenario (e.g. `historic`, `ssp245`)."
    ),
    crop: str | None = Query(
        default=None, description="Crop or livestock commodity (e.g. `maize`)."
    ),
    hazard: str | None = Query(
        default=None, description="Hazard grouping (e.g. `dry`, `heat`, `dry+heat+wet`)."
    ),
    hazard_vars: str | None = Query(
        default=None, description="Hazard indices combination (e.g. `NDWS+NTx35+NDWL0`)."
    ),
    admin_level: str | None = Query(
        default=None, description="Administrative level of the records: `0`, `1` or `2`."
    ),
    format: OutputFormat = Query(default=OutputFormat.JSON, description="Response format."),
    limit: int | None = Query(
        default=None, ge=1, description="Page size (defaults to the configured page size)."
    ),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
    service: HazardExposureService = Depends(get_hazard_exposure_service),
) -> HazardExposureResponse | StreamingResponse:
    """Serve HU-001.

    Every query parameter actually received (including unregistered ones) is handed to
    the service, which validates it against the dataset definition in the catalog.

    Args:
        request: Incoming request, used to collect every query parameter.
        dataset_id: Public dataset identifier.
        country: ISO3 country code (required).
        period: Period analysed (required).
        source: Optional data source partition.
        variable: Optional exposure variable partition.
        season_type: Optional calendar partition.
        model: Optional climate model partition.
        severity: Optional severity partition.
        hazard_type: Optional file type selector.
        scenario: Optional emissions scenario.
        crop: Optional crop or livestock commodity.
        hazard: Optional hazard grouping.
        hazard_vars: Optional hazard indices combination.
        admin_level: Optional administrative level.
        format: Response format.
        limit: Page size.
        offset: Records to skip.
        service: HU-001 service.

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
        return HazardExposureResponse.from_result(result)

    file_name = (
        f"{dataset_id}_{result.applied_filters.get('country', 'all')}.{exporter.file_extension}"
    )
    return StreamingResponse(
        exporter.export(result),
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
