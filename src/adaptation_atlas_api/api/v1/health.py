"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from adaptation_atlas_api.api.deps import Container, get_container
from adaptation_atlas_api.api.schemas.common import HealthResponse

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Returns the service status, version and the number of registered datasets.",
)
def health(container: Container = Depends(get_container)) -> HealthResponse:
    """Report that the service is up.

    Args:
        container: Application container with settings and catalog.

    Returns:
        The health payload.
    """
    settings = container.settings
    return HealthResponse(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        datasets=len(container.catalog.list_all()),
    )
