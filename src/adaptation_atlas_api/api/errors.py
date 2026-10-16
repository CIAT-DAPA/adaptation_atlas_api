"""Translation of domain exceptions into uniform HTTP error responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from adaptation_atlas_api.api.schemas.common import ErrorResponse
from adaptation_atlas_api.domain.exceptions import (
    CatalogConfigurationError,
    DatasetNotFoundError,
    DomainError,
    QueryExecutionError,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

# Status code of validation failures (named locally to stay independent of Starlette renames).
HTTP_422_UNPROCESSABLE = 422

# HTTP status returned for each domain exception; anything else is a 422.
STATUS_BY_EXCEPTION: dict[type[DomainError], int] = {
    DatasetNotFoundError: status.HTTP_404_NOT_FOUND,
    SourceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    QueryExecutionError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    CatalogConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _status_for(error: DomainError) -> int:
    """Pick the HTTP status for a domain error, walking its class hierarchy.

    Args:
        error: The raised domain error.

    Returns:
        The HTTP status code.
    """
    # Check the most specific class first, then its parents.
    for exception_type in type(error).__mro__:
        # Stop at the first class with an explicit status mapping.
        if exception_type in STATUS_BY_EXCEPTION:
            return STATUS_BY_EXCEPTION[exception_type]
    return HTTP_422_UNPROCESSABLE


async def domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
    """Render a :class:`DomainError` as a JSON error response.

    Args:
        _: The incoming request (unused).
        error: The raised domain error.

    Returns:
        A JSON response with the uniform error schema.
    """
    http_status = _status_for(error)

    # Server-side failures are logged with their traceback; client errors are not.
    if http_status >= 500:
        logger.error("Domain error %s: %s", error.code, error.message, exc_info=True)

    payload = ErrorResponse(
        code=error.code,
        message=error.message,
        parameter=error.parameter,
        allowed_values=error.allowed_values,
    )
    return JSONResponse(status_code=http_status, content=payload.model_dump())


async def request_validation_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    """Render FastAPI parameter validation failures with the uniform error schema.

    Args:
        _: The incoming request (unused).
        error: The validation error raised by FastAPI.

    Returns:
        A 422 JSON response naming the first invalid parameter.
    """
    details = error.errors()
    first = details[0] if details else {}
    location = [str(item) for item in first.get("loc", []) if item not in ("query", "path", "body")]
    parameter = ".".join(location) if location else None
    message = first.get("msg", "Invalid request.")

    payload = ErrorResponse(
        code="invalid_parameter",
        message=f"Invalid value for parameter '{parameter}': {message}" if parameter else message,
        parameter=parameter,
    )
    return JSONResponse(status_code=HTTP_422_UNPROCESSABLE, content=payload.model_dump())


async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    """Render any unhandled exception as a controlled 500 without internal details.

    Args:
        _: The incoming request (unused).
        error: The unexpected exception.

    Returns:
        A generic 500 JSON response.
    """
    logger.exception("Unhandled error: %s", error)
    payload = ErrorResponse(code="internal_error", message="An unexpected error occurred.")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump()
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every error handler to ``app``.

    Args:
        app: The FastAPI application.
    """
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
