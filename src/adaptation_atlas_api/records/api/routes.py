"""Generic records endpoint: serves every tabular dataset registered in the catalog."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import StreamingResponse

from adaptation_atlas_api.records.application.tabular_service import TabularDatasetService
from adaptation_atlas_api.records.domain.query import OutputFormat
from adaptation_atlas_api.records.infrastructure.registry import resolve_tabular_exporter
from adaptation_atlas_api.shared.api.deps import get_container
from adaptation_atlas_api.shared.api.schemas import ERROR_RESPONSES, RecordsResponse
from adaptation_atlas_api.shared.domain.exceptions import (
    ConflictingFilterError,
    InvalidFiltersJsonError,
)

router = APIRouter(prefix="/datasets", tags=["Records"])


def get_tabular_service(request: Request) -> TabularDatasetService:
    """Return the generic tabular records service from the application container.

    Args:
        request: The incoming request.

    Returns:
        The generic tabular records service.
    """
    service: TabularDatasetService = get_container(request).tabular_service
    return service


DESCRIPTION = """
Retrieve records from any tabular dataset published in the catalog.

**Filters are dataset specific.** Call `GET /datasets/{dataset_id}` first: it lists the
filters the dataset accepts, which ones are required, their valid values, the fields of
every record, and ready-to-use examples (`example_query` and `example_filters`).

Filters can be passed in two equivalent ways, which may be combined:

* as a JSON object in the `filters` parameter (convenient from Swagger UI):
  `filters={"country":"ETH","indicator":"haz_freq_ensemble","scenario":["ssp245","ssp585"]}`
* as ordinary query parameters, one per filter (convenient from a browser, curl, Python or R):
  `?country=ETH&indicator=haz_freq_ensemble&scenario=ssp245`

**Several values for one filter** are combined with OR: a JSON array
(`"crop":["maize","bean"]`), a repeated parameter (`crop=maize&crop=bean`) or a comma
separated list (`crop=maize,bean`). Sending the same filter both ways with different values
is rejected.

Behaviour shared by every dataset:

* Parameters not registered for the dataset, missing required parameters and values
  outside the controlled vocabularies return **422** naming the parameter and its valid
  values; no query is executed.
* Valid filters with no matching record return **200** with an empty `data` list.
* `format=csv` streams the same logical records and fields as the JSON response.
* Results are paginated with `limit` and `offset`; `metadata.total_count` reports the
  total number of matching records.
"""


RawFilters = dict[str, str | list[str] | None]


def collect_query_parameters(request: Request) -> RawFilters:
    """Collect every query parameter, keeping repeated parameters as lists.

    Args:
        request: Incoming request.

    Returns:
        Parameter name -> value, or list of values when the parameter was repeated.
    """
    collected: RawFilters = {}

    # Walk every (name, value) pair, grouping repeated names into lists.
    for name, value in request.query_params.multi_items():
        existing = collected.get(name)
        # The first occurrence is stored as a plain string.
        if existing is None:
            collected[name] = value
        # Further occurrences turn the entry into a list.
        elif isinstance(existing, list):
            existing.append(value)
        else:
            collected[name] = [existing, value]
    return collected


def _scalar_to_text(key: str, value: object) -> str:
    """Convert a JSON scalar into the text form used by the validator.

    Args:
        key: Filter name, used in error messages.
        value: JSON value.

    Returns:
        The value as text.

    Raises:
        InvalidFiltersJsonError: If the value is not a string, number or boolean.
    """
    # Booleans are checked before integers because bool is a subclass of int.
    if isinstance(value, bool):
        return "true" if value else "false"
    # Strings and numbers are rendered as text.
    if isinstance(value, (str, int, float)):
        return str(value)
    raise InvalidFiltersJsonError(
        f"Filter '{key}' in parameter 'filters' must be a string, number, boolean or a list "
        "of those.",
        parameter="filters",
    )


def parse_filters_json(raw: str) -> dict[str, str | list[str]]:
    """Parse the ``filters`` parameter into a mapping of filter name to value(s).

    Args:
        raw: JSON text received in the ``filters`` query parameter.

    Returns:
        Filter name -> value as text, or list of texts when a JSON array was given.

    Raises:
        InvalidFiltersJsonError: If the text is not valid JSON, not an object, or holds
            nested objects or null values.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidFiltersJsonError(
            f"Parameter 'filters' is not valid JSON: {error.msg} (position {error.pos}).",
            parameter="filters",
        ) from error

    # Only a JSON object maps naturally to named filters.
    if not isinstance(parsed, dict):
        raise InvalidFiltersJsonError(
            "Parameter 'filters' must be a JSON object such as "
            '{"country":"KEN","period":"historic"}.',
            parameter="filters",
        )

    result: dict[str, str | list[str]] = {}
    # Convert every value to text (or list of texts) for the validator.
    for key, value in parsed.items():
        # JSON arrays become lists of texts, each element validated separately.
        if isinstance(value, list):
            result[str(key)] = [_scalar_to_text(str(key), item) for item in value]
        else:
            result[str(key)] = _scalar_to_text(str(key), value)
    return result


def _as_list(value: str | list[str] | None) -> list[str]:
    """Normalize a raw filter value into a list of non-blank strings.

    Args:
        value: Raw value from the query string or the JSON object.

    Returns:
        The non-blank values, as a list.
    """
    # Absent values contribute nothing.
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    return [item for item in items if item.strip()]


def merge_filters(query_params: RawFilters, filters_json: str | None) -> RawFilters:
    """Combine loose query parameters with the ``filters`` JSON object.

    Args:
        query_params: Every query parameter received, including control parameters.
        filters_json: Raw value of the ``filters`` parameter, or ``None``.

    Returns:
        The merged mapping handed to the service (control parameters included, they are
        ignored by the validator).

    Raises:
        ConflictingFilterError: If a filter appears in both places with different values.
    """
    merged: RawFilters = dict(query_params)

    # Without the JSON parameter the loose query parameters are used as they are.
    if filters_json is None or not filters_json.strip():
        return merged

    # JSON filters are added one by one, refusing contradictory duplicates.
    for key, value in parse_filters_json(filters_json).items():
        existing = _as_list(merged.get(key))
        incoming = _as_list(value)
        # The same filter sent twice is only accepted when both values agree.
        if existing and existing != incoming:
            raise ConflictingFilterError(
                f"Filter '{key}' was sent both in 'filters' ({incoming}) and as a query "
                f"parameter ({existing}) with different values.",
                parameter=key,
            )
        merged[key] = value
    return merged


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
)
def get_records(
    request: Request,
    dataset_id: str = Path(
        ..., description="Public dataset identifier.", examples=["hazard_exposure"]
    ),
    filters: str | None = Query(
        default=None,
        description=(
            "Dataset filters as a JSON object, e.g. "
            '`{"country":"KEN","period":"historic","crop":["maize","bean"]}`. '
            "Arrays select several values. See `GET /datasets/{dataset_id}` for the filters "
            "of each dataset."
        ),
        examples=['{"country":"KEN","period":"historic","crop":["maize","bean"]}'],
    ),
    format: OutputFormat = Query(default=OutputFormat.JSON, description="Response format."),
    limit: int | None = Query(
        default=None, ge=1, description="Page size (defaults to the configured page size)."
    ),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
    service: TabularDatasetService = Depends(get_tabular_service),
) -> RecordsResponse | StreamingResponse:
    """Serve records of a tabular dataset.

    Filters from the ``filters`` JSON object and from the remaining query parameters are
    merged and handed to the service, which validates them against the catalog.

    Args:
        request: Incoming request, used to collect every query parameter.
        dataset_id: Public dataset identifier.
        filters: Optional JSON object with dataset filters.
        format: Response format.
        limit: Page size.
        offset: Records to skip.
        service: Generic tabular service.

    Returns:
        The JSON payload, or a streaming CSV response.
    """
    raw_filters = merge_filters(collect_query_parameters(request), filters)
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

    suffix = "_".join(
        "-".join(value) if isinstance(value, list) else value
        for value in result.applied_filters.values()
        if value
    )[:80]
    file_name = f"{dataset_id}_{suffix or 'all'}.{exporter.file_extension}"
    return StreamingResponse(
        exporter.export(result),
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
