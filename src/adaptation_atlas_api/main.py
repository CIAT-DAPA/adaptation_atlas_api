"""FastAPI application factory.

Run locally with::

    uv run uvicorn adaptation_atlas_api.main:app --reload
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from adaptation_atlas_api.catalog.api import health as health_routes
from adaptation_atlas_api.catalog.api import routes as catalog_routes
from adaptation_atlas_api.container import build_container
from adaptation_atlas_api.raster.api import routes as raster_routes
from adaptation_atlas_api.records.api import routes as records_routes
from adaptation_atlas_api.shared.api.errors import register_exception_handlers
from adaptation_atlas_api.shared.infrastructure.config import Settings, get_settings
from adaptation_atlas_api.shared.infrastructure.logging import configure_logging

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service status."},
    {
        "name": "Catalog",
        "description": "Discover published datasets, their filters and valid values.",
    },
    {
        "name": "Records",
        "description": (
            "Query records of any tabular dataset by its identifier; filters are defined per "
            "dataset in the catalog (HU-001 hazard exposure, climate, exposure, impacts...)."
        ),
    },
    {
        "name": "Raster",
        "description": (
            "Clip, reproject, resample, convert and aggregate any raster dataset by its "
            "identifier (HU-003 climate change scenarios, HU-004 observed climate)."
        ),
    },
]

API_DESCRIPTION = """
The **Adaptation Atlas API** gives standardized access to prioritized climate, hazard and
exposure datasets produced by the Adaptation Atlas and stored in AWS S3.

Consumers query datasets through business-oriented parameters (country, period, crop,
severity...) without knowing the storage layout. Responses are JSON by default and CSV on
request.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Optional settings override (used by tests); defaults to the ``.env`` values.

    Returns:
        The configured application.
    """
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """Load the catalog and wire adapters at startup.

        Args:
            application: The application being started.

        Yields:
            Control to the server while the application runs.
        """
        logger.info("Starting %s (%s)", resolved_settings.app_name, resolved_settings.app_env)
        application.state.container = build_container(resolved_settings)
        yield
        logger.info("Shutting down %s", resolved_settings.app_name)

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    # Every slice contributes its own router under the versioned prefix.
    for slice_router in (
        health_routes.router,
        catalog_routes.router,
        records_routes.router,
        raster_routes.router,
    ):
        app.include_router(slice_router, prefix=resolved_settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Redirect the root path to the interactive API documentation.

        Returns:
            A temporary redirect to the Swagger UI page.
        """
        return RedirectResponse(url="/docs", status_code=307)

    return app


app = create_app()
