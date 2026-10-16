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


class FilterNotApplicableError(DomainError):
    """A registered filter targets a column absent from the files selected by the query."""

    code = "filter_not_applicable"


class UnsupportedFormatError(DomainError):
    """The requested output format is not supported by the dataset."""

    code = "unsupported_format"


class InvalidPaginationError(DomainError):
    """The supplied ``limit`` or ``offset`` is outside the accepted range."""

    code = "invalid_pagination"


class SourceUnavailableError(DomainError):
    """The registered source asset cannot be read (storage failure)."""

    code = "source_unavailable"


class QueryExecutionError(DomainError):
    """The query engine failed while executing a valid request."""

    code = "query_execution_error"


class CatalogConfigurationError(DomainError):
    """A dataset definition in the catalog is invalid or inconsistent."""

    code = "catalog_configuration_error"
