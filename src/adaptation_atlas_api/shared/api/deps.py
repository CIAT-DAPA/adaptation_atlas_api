"""FastAPI dependencies shared by every slice.

The container built by :mod:`adaptation_atlas_api.container` is stored on the
application state; slices read the service they need from it without importing the
composition root (which would make them depend on every other slice).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from adaptation_atlas_api.shared.infrastructure.config import Settings


def get_container(request: Request) -> Any:
    """Return the container stored on the application state.

    Args:
        request: The incoming request.

    Returns:
        The application container (see :mod:`adaptation_atlas_api.container`).
    """
    return request.app.state.container


def get_settings_dependency(request: Request) -> Settings:
    """Return the application settings.

    Args:
        request: The incoming request.

    Returns:
        The settings instance.
    """
    settings: Settings = get_container(request).settings
    return settings
