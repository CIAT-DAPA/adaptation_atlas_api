"""Dataset discovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from adaptation_atlas_api.api.deps import get_catalog_service
from adaptation_atlas_api.api.schemas.catalog import DatasetDetail, DatasetSummary
from adaptation_atlas_api.api.schemas.common import ERROR_RESPONSES
from adaptation_atlas_api.domain.services.catalog import CatalogService

router = APIRouter(prefix="/datasets", tags=["Catalog"])


@router.get(
    "",
    response_model=list[DatasetSummary],
    summary="List published datasets",
    description=(
        "Returns every dataset registered in the catalog, optionally restricted to one "
        "thematic domain. Use the identifier of a dataset in "
        "`GET /datasets/{dataset_id}/records`; no knowledge of the storage layout is required."
    ),
)
def list_datasets(
    domain: str | None = Query(
        default=None,
        description="Only return datasets of this domain (e.g. `climate`, `exposure`).",
        examples=["climate"],
    ),
    service: CatalogService = Depends(get_catalog_service),
) -> list[DatasetSummary]:
    """List registered datasets, optionally filtered by domain.

    Args:
        domain: Thematic domain to filter by, or ``None`` for every dataset.
        service: Catalog service.

    Returns:
        Dataset summaries sorted by identifier.
    """
    return [DatasetSummary.from_domain(dataset) for dataset in service.list_datasets(domain=domain)]


@router.get(
    "/{dataset_id}",
    response_model=DatasetDetail,
    responses={404: ERROR_RESPONSES[404]},
    summary="Describe a dataset",
    description=(
        "Returns the filters accepted by the dataset with their valid values, the fields of "
        "every record and, when partition discovery is enabled, the partition combinations "
        "that actually exist in storage. Use it to build queries for "
        "`GET /datasets/{dataset_id}/records`."
    ),
)
def get_dataset(
    dataset_id: str = Path(
        ..., description="Public dataset identifier.", examples=["hazard_exposure"]
    ),
    service: CatalogService = Depends(get_catalog_service),
) -> DatasetDetail:
    """Describe one dataset.

    Args:
        dataset_id: Public dataset identifier.
        service: Catalog service.

    Returns:
        The dataset detail view.
    """
    return DatasetDetail.from_availability(service.describe_dataset(dataset_id))
