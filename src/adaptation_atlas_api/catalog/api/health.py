"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from adaptation_atlas_api.catalog.api.routes import get_catalog_service
from adaptation_atlas_api.catalog.application.catalog_service import CatalogService
from adaptation_atlas_api.shared.api.deps import get_settings_dependency
from adaptation_atlas_api.shared.api.schemas import HealthResponse
from adaptation_atlas_api.shared.infrastructure.config import Settings

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description="Returns the service status, version and the number of registered datasets.",
)
def health(
    settings: Settings = Depends(get_settings_dependency),
    catalog_service: CatalogService = Depends(get_catalog_service),
) -> HealthResponse:
    """Report that the service is up.

    Args:
        settings: Application settings.
        catalog_service: Catalog service used to count the registered datasets.

    Returns:
        The health payload.
    """
    return HealthResponse(
        name=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        datasets=len(catalog_service.list_datasets()),
    )
