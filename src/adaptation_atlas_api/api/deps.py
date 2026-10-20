"""Dependency injection: wires adapters to services once and exposes them to routers."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Request

from adaptation_atlas_api.adapters.catalog.yaml_catalog import YamlCatalogRepository
from adaptation_atlas_api.adapters.registry import (
    resolve_raster_reader,
    resolve_reader,
    resolve_tabular_reader,
)
from adaptation_atlas_api.adapters.storage.local_storage import LocalStorage
from adaptation_atlas_api.adapters.storage.partition_index import StoragePartitionIndex
from adaptation_atlas_api.adapters.storage.s3_storage import S3Storage
from adaptation_atlas_api.domain.exceptions import CatalogConfigurationError
from adaptation_atlas_api.domain.services.catalog import CatalogService
from adaptation_atlas_api.domain.services.raster_dataset import RasterDatasetService
from adaptation_atlas_api.domain.services.tabular_dataset import TabularDatasetService
from adaptation_atlas_api.infrastructure.config import Settings
from adaptation_atlas_api.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.ports.partition_index import PartitionIndex
from adaptation_atlas_api.ports.raster_reader import RasterReader
from adaptation_atlas_api.ports.storage import Storage
from adaptation_atlas_api.ports.tabular_reader import TabularReader

logger = logging.getLogger(__name__)


@dataclass
class Container:
    """Holds the singletons shared by every request.

    Attributes:
        settings: Application settings.
        storage: Storage adapter.
        catalog: Catalog repository.
        partition_index: Index of existing partition combinations.
        tabular_reader: Reader used for tabular datasets.
        raster_reader: Reader used for raster datasets.
        catalog_service: Dataset discovery use cases.
        tabular_service: Generic tabular records use case (serves HU-001 and others).
        raster_service: Generic raster use case (serves HU-003 and HU-004).
    """

    settings: Settings
    storage: Storage
    catalog: CatalogRepository
    partition_index: PartitionIndex
    tabular_reader: TabularReader
    raster_reader: RasterReader
    catalog_service: CatalogService
    tabular_service: TabularDatasetService
    raster_service: RasterDatasetService


def build_storage(settings: Settings) -> Storage:
    """Instantiate the storage adapter selected by ``STORAGE_BACKEND``.

    Args:
        settings: Application settings.

    Returns:
        The storage adapter.

    Raises:
        CatalogConfigurationError: If the local backend is selected without a root.
    """
    # The local backend is meant for development and tests.
    if settings.storage_backend == "local":
        # A local root is mandatory to know where the files are.
        if settings.local_storage_root is None:
            raise CatalogConfigurationError(
                "LOCAL_STORAGE_ROOT must be set when STORAGE_BACKEND=local."
            )
        return LocalStorage(settings.local_storage_root)

    return S3Storage(
        bucket=settings.s3_bucket,
        region=settings.aws_region,
        access_key_id=settings.aws_access_key_id,
        secret_access_key=settings.aws_secret_access_key,
        session_token=settings.aws_session_token,
        endpoint_url=settings.s3_endpoint_url,
    )


def build_container(settings: Settings) -> Container:
    """Create every adapter and service and load the catalog.

    Args:
        settings: Application settings.

    Returns:
        The populated container.
    """
    storage = build_storage(settings)
    catalog = YamlCatalogRepository(settings.catalog_path, storage=storage)
    catalog.load()

    partition_index = StoragePartitionIndex(
        storage, ttl_seconds=settings.partition_cache_ttl_seconds
    )

    # Validate every reader key declared in the catalog before serving requests.
    for dataset in catalog.list_all():
        resolve_reader(dataset)

    reader_class = resolve_tabular_reader("duckdb_parquet")
    tabular_reader = reader_class(  # type: ignore[call-arg]
        storage,
        memory_limit=settings.duckdb_memory_limit,
        threads=settings.duckdb_threads,
        query_timeout_seconds=settings.query_timeout_seconds,
        max_skipped_files=settings.max_skipped_files,
    )

    raster_reader_class = resolve_raster_reader("rasterio_geotiff")
    raster_reader = raster_reader_class(storage, max_pixels=settings.raster_max_pixels)  # type: ignore[call-arg]

    catalog_service = CatalogService(catalog, partition_index, api_prefix=settings.api_prefix)
    tabular_service = TabularDatasetService(
        catalog,
        tabular_reader,
        max_rows=settings.max_rows_per_response,
        default_page_size=settings.default_page_size,
    )
    raster_service = RasterDatasetService(
        catalog,
        raster_reader,
        max_pixels=settings.raster_max_pixels,
        max_layers=settings.raster_max_layers,
        max_points=settings.raster_max_points,
    )
    logger.info(
        "Container ready: backend=%s datasets=%d", settings.storage_backend, len(catalog.list_all())
    )
    return Container(
        settings=settings,
        storage=storage,
        catalog=catalog,
        partition_index=partition_index,
        tabular_reader=tabular_reader,
        raster_reader=raster_reader,
        catalog_service=catalog_service,
        tabular_service=tabular_service,
        raster_service=raster_service,
    )


def get_container(request: Request) -> Container:
    """Return the container stored on the application state.

    Args:
        request: The incoming request.

    Returns:
        The application container.
    """
    return request.app.state.container


def get_settings_dependency(request: Request) -> Settings:
    """Return the application settings.

    Args:
        request: The incoming request.

    Returns:
        The settings instance.
    """
    return get_container(request).settings


def get_catalog_service(request: Request) -> CatalogService:
    """Return the catalog service.

    Args:
        request: The incoming request.

    Returns:
        The catalog service.
    """
    return get_container(request).catalog_service


def get_tabular_service(request: Request) -> TabularDatasetService:
    """Return the generic tabular records service.

    Args:
        request: The incoming request.

    Returns:
        The tabular dataset service.
    """
    return get_container(request).tabular_service


def get_raster_service(request: Request) -> RasterDatasetService:
    """Return the generic raster service.

    Args:
        request: The incoming request.

    Returns:
        The raster dataset service.
    """
    return get_container(request).raster_service
