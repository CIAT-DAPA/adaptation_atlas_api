"""Aggregates every v1 router."""

from __future__ import annotations

from fastapi import APIRouter

from adaptation_atlas_api.api.v1 import catalog, hazard_exposure, health

router = APIRouter()
router.include_router(health.router)
router.include_router(catalog.router)
router.include_router(hazard_exposure.router)
