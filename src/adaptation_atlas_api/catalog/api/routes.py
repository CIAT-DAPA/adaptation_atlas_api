"""Dataset discovery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Request

from adaptation_atlas_api.catalog.api.schemas import DatasetDetail, DatasetSummary
from adaptation_atlas_api.catalog.application.catalog_service import CatalogService
from adaptation_atlas_api.shared.api.deps import get_container
from adaptation_atlas_api.shared.api.schemas import ERROR_RESPONSES

router = APIRouter(prefix="/datasets", tags=["Catalog"])


def get_catalog_service(request: Request) -> CatalogService:
    """Return the catalog service from the application container.

    Args:
        request: The incoming request.

    Returns:
        The catalog service.
    """
    service: CatalogService = get_container(request).catalog_service
    return service


@router.get(
    "",
    response_model=list[DatasetSummary],
    responses={422: ERROR_RESPONSES[422]},
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
    type: str | None = Query(
        default=None,
        description=(
            "Only return datasets of this kind: `tabular` (served by "
            "`GET /datasets/{dataset_id}/records`) or `raster` (served by "
            "`POST /datasets/{dataset_id}/raster`)."
        ),
        examples=["raster"],
    ),
    service: CatalogService = Depends(get_catalog_service),
) -> list[DatasetSummary]:
    """List registered datasets, optionally filtered by domain and kind.

    Args:
        domain: Thematic domain to filter by, or ``None`` for every dataset.
        type: Dataset kind to filter by, or ``None`` for every kind.
        service: Catalog service.

    Returns:
        Dataset summaries sorted by identifier.
    """
    return [
        DatasetSummary.from_domain(dataset)
        for dataset in service.list_datasets(domain=domain, dataset_type=type)
    ]


@router.get(
    "/{dataset_id}",
    response_model=DatasetDetail,
    responses={404: ERROR_RESPONSES[404]},
    summary="Describe a dataset",
    description=(
        "Returns the filters accepted by the dataset with their valid values and, when "
        "partition discovery is enabled, the partition combinations that actually exist in "
        "storage. Tabular datasets also list the fields of every record (use them with "
        "`GET /datasets/{dataset_id}/records`); raster datasets describe their CRS, "
        "resolution, variables, units and supported aggregations, plus an example body for "
        "`POST /datasets/{dataset_id}/raster`."
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
