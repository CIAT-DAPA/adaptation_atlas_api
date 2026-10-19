"""Schemas shared by every endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
