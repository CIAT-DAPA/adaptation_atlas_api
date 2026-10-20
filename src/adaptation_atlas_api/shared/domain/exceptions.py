"""Domain exceptions raised by services and validation rules.

Every exception carries a machine readable ``code`` and, when relevant, the
offending ``parameter`` and its ``allowed_values`` so the API layer can build a
uniform error response without leaking storage internals.
"""

from __future__ import annotations

from collections.abc import Sequence


class DomainError(Exception):
    """Base class for every business error raised by the domain layer.

    Attributes:
        code: Short machine readable identifier of the error.
        message: Human readable description safe to expose to clients.
        parameter: Name of the request parameter that caused the error, if any.
        allowed_values: Valid values for ``parameter``, when they are known.
    """

    code: str = "domain_error"

    def __init__(
        self,
        message: str,
        *,
        parameter: str | None = None,
        allowed_values: Sequence[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.parameter = parameter
        self.allowed_values = list(allowed_values) if allowed_values is not None else None


class DatasetNotFoundError(DomainError):
    """The requested dataset identifier is not registered in the catalog."""

    code = "dataset_not_found"


class UnsupportedFilterError(DomainError):
    """The request contains a filter that is not registered for the dataset."""

    code = "unsupported_filter"


class MissingRequiredFilterError(DomainError):
    """A filter marked as required by the catalog was not supplied."""

    code = "missing_required_filter"


class InvalidFilterValueError(DomainError):
    """A supplied filter value is outside the controlled vocabulary."""

    code = "invalid_filter_value"


class InvalidFiltersJsonError(DomainError):
    """The ``filters`` parameter is not a JSON object of scalar values."""

    code = "invalid_filters_json"


class ConflictingFilterError(DomainError):
    """The same filter was sent in ``filters`` and as a query parameter with different values."""

    code = "conflicting_filter"


class FilterNotApplicableError(DomainError):
    """A registered filter targets a column absent from the files selected by the query."""

    code = "filter_not_applicable"


class UnsupportedFormatError(DomainError):
    """The requested output format is not supported by the dataset."""

    code = "unsupported_format"


class InvalidPaginationError(DomainError):
    """The supplied ``limit`` or ``offset`` is outside the accepted range."""

    code = "invalid_pagination"


class WrongDatasetTypeError(DomainError):
    """The dataset exists but is served by another endpoint (tabular vs raster)."""

    code = "wrong_dataset_type"


class InvalidGeometryError(DomainError):
    """The supplied spatial feature is malformed, empty or invalid."""

    code = "invalid_geometry"


class InvalidBoundingBoxError(DomainError):
    """The supplied bounding box is missing values, reversed or out of range."""

    code = "invalid_bbox"


class InvalidCoordinatesError(DomainError):
    """A supplied coordinate is outside the accepted geographic ranges."""

    code = "invalid_coordinates"


class NoCoverageError(DomainError):
    """The geographic selection does not intersect the dataset coverage."""

    code = "no_coverage"


class InvalidDateRangeError(DomainError):
    """The supplied dates are malformed or the start is after the end."""

    code = "invalid_date_range"


class PeriodOutsideCoverageError(DomainError):
    """No layer of the dataset falls inside the requested period."""

    code = "period_outside_coverage"


class UnsupportedCombinationError(DomainError):
    """The combination of filter values does not exist in storage."""

    code = "unsupported_combination"


class UnsupportedCrsError(DomainError):
    """The requested target coordinate reference system is not supported."""

    code = "unsupported_crs"


class UnsupportedResolutionError(DomainError):
    """The requested target resolution is not permitted for the dataset."""

    code = "unsupported_resolution"


class UnsupportedResamplingError(DomainError):
    """The requested resampling method is not supported."""

    code = "unsupported_resampling"


class UnsupportedUnitError(DomainError):
    """No conversion rule is registered for the requested target unit."""

    code = "unsupported_unit"


class UnsupportedAggregationError(DomainError):
    """The requested temporal aggregation is unknown, unsupported or incomplete."""

    code = "unsupported_aggregation"


class RequestTooLargeError(DomainError):
    """The request would read or produce more data than the service allows."""

    code = "request_too_large"


class SourceUnavailableError(DomainError):
    """The registered source asset cannot be read (storage failure)."""

    code = "source_unavailable"


class QueryExecutionError(DomainError):
    """The query engine failed while executing a valid request."""

    code = "query_execution_error"


class CatalogConfigurationError(DomainError):
    """A dataset definition in the catalog is invalid or inconsistent."""

    code = "catalog_configuration_error"
