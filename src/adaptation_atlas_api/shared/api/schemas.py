"""Schemas shared by every endpoint: errors, tabular responses and health."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adaptation_atlas_api.shared.domain.results import TabularResult


class ErrorResponse(BaseModel):
    """Uniform error payload returned for every handled failure.

    Attributes:
        code: Machine readable error identifier.
        message: Human readable description safe for clients.
        parameter: Request parameter that caused the error, when applicable.
        allowed_values: Valid values for ``parameter``, when known.
    """

    code: str = Field(..., examples=["invalid_filter_value"])
    message: str = Field(..., examples=["Invalid value 'XXX' for parameter 'country'."])
    parameter: str | None = Field(default=None, examples=["country"])
    allowed_values: list[str] | None = Field(default=None, examples=[["KEN", "ETH", "NGA"]])


class ResponseMetadata(BaseModel):
    """Metadata describing a tabular response page.

    Attributes:
        dataset_id: Public identifier of the dataset queried.
        filters: Public filter name -> canonical value (or list of values) actually applied.
        total_count: Total matching records before pagination.
        returned_count: Records included in this page.
        limit: Page size applied.
        offset: Records skipped.
        has_more: Whether another page exists.
        source_files: Number of source files read by the query.
        skipped_files: Partition paths of matched source files that could not be read
            (corrupt or truncated objects) and were left out. Empty when the response is
            complete; when not empty, ``partial`` is ``True`` and the data team should
            be notified.
        partial: Whether the response is missing data because of skipped files.
    """

    dataset_id: str
    filters: dict[str, str | list[str]]
    total_count: int
    returned_count: int
    limit: int
    offset: int
    has_more: bool
    source_files: int
    skipped_files: list[str] = Field(
        default_factory=list,
        examples=[
            [
                "variable=vop_intld15/period=jagermeyr/model=ENSEMBLE/severity=severe/interaction.parquet"
            ]
        ],
    )
    partial: bool = False


class RecordsResponse(BaseModel):
    """JSON payload returned by ``GET /datasets/{dataset_id}/records``.

    The keys of every record are the fields declared by the dataset in the catalog (see
    ``GET /datasets/{dataset_id}``), or every source column when the dataset does not
    declare explicit fields.

    Attributes:
        metadata: Pagination and filter metadata.
        data: Matching records (empty list when nothing matches).
    """

    metadata: ResponseMetadata
    data: list[dict[str, Any]] = Field(
        ...,
        examples=[
            [
                {
                    "iso3": "KEN",
                    "admin0_name": "Kenya",
                    "admin1_name": "Nakuru",
                    "admin2_name": None,
                    "timeframe": "historic",
                    "crop": "maize",
                    "hazard": "dry+heat",
                    "severity": "moderate",
                    "exposure_unit": "intld15",
                    "value": 102546.0,
                }
            ]
        ],
    )

    @classmethod
    def from_result(cls, result: TabularResult) -> RecordsResponse:
        """Build the payload from a domain result.

        Args:
            result: Result returned by the service.

        Returns:
            The response model.
        """
        metadata = ResponseMetadata(
            dataset_id=result.dataset_id,
            filters=result.applied_filters,
            total_count=result.total_count,
            returned_count=result.returned_count,
            limit=result.limit,
            offset=result.offset,
            has_more=result.has_more,
            source_files=result.source_files,
            skipped_files=list(result.skipped_files),
            partial=result.is_partial,
        )
        return cls(metadata=metadata, data=result.records)


class HealthResponse(BaseModel):
    """Payload of the health endpoint.

    Attributes:
        status: Always ``ok`` when the service is up.
        name: Application name.
        version: Application version.
        environment: Deployment environment.
        datasets: Number of datasets registered in the catalog.
    """

    status: str = "ok"
    name: str
    version: str
    environment: str
    datasets: int


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "The dataset is not registered."},
    422: {"model": ErrorResponse, "description": "A request parameter is invalid."},
    503: {"model": ErrorResponse, "description": "The source data is unavailable."},
}
