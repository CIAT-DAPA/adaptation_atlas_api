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

from adaptation_atlas_api.api.deps import build_container
from adaptation_atlas_api.api.errors import register_exception_handlers
from adaptation_atlas_api.api.v1.router import router as v1_router
from adaptation_atlas_api.infrastructure.config import Settings, get_settings
from adaptation_atlas_api.infrastructure.logging import configure_logging

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {"name": "Health", "description": "Service status."},
    {
        "name": "Catalog",
        "description": "Discover published datasets, their filters and valid values.",
    },
    {
        "name": "Hazard exposure",
        "description": "HU-001 · Hazard exposure (hazard frequency × exposure) by territory.",
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
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(v1_router, prefix=resolved_settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Redirect the root path to the interactive API documentation.

        Returns:
            A temporary redirect to the Swagger UI page.
        """
        return RedirectResponse(url="/docs", status_code=307)

    return app


app = create_app()
