"""Shared pytest fixtures: synthetic storage, settings, adapters and the test client."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adaptation_atlas_api.main import create_app
from adaptation_atlas_api.raster.application.raster_service import RasterDatasetService
from adaptation_atlas_api.raster.infrastructure.rasterio_reader import RasterioRasterReader
from adaptation_atlas_api.records.infrastructure.duckdb_reader import DuckDBTabularReader
from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition
from adaptation_atlas_api.shared.infrastructure.config import Settings
from adaptation_atlas_api.shared.infrastructure.local_storage import LocalStorage
from adaptation_atlas_api.shared.infrastructure.yaml_catalog import YamlCatalogRepository
from tests.fixtures.synthetic_data import build_storage_tree
from tests.fixtures.synthetic_rasters import build_raster_tree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_CATALOG = PROJECT_ROOT / "catalog"
TEST_CATALOG = Path(__file__).resolve().parent / "fixtures" / "catalog"


@pytest.fixture(scope="session")
def storage_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create the synthetic bucket tree (Parquet and GeoTIFF) once per test session."""
    root = build_storage_tree(tmp_path_factory.mktemp("bucket"))
    return build_raster_tree(root)


@pytest.fixture(scope="session")
def local_storage(storage_root: Path) -> LocalStorage:
    """Local storage adapter pointing at the synthetic tree."""
    return LocalStorage(storage_root)


@pytest.fixture(scope="session")
def catalog_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Catalog directory combining the project catalog with the test-only datasets."""
    target = tmp_path_factory.mktemp("catalog")
    shutil.copytree(PROJECT_CATALOG, target / "project")
    shutil.copytree(TEST_CATALOG, target / "tests")
    return target


@pytest.fixture(scope="session")
def catalog(local_storage: LocalStorage, catalog_path: Path) -> YamlCatalogRepository:
    """Catalog loaded from the project definitions plus the test fixtures."""
    repository = YamlCatalogRepository(catalog_path, storage=local_storage)
    repository.load()
    return repository


@pytest.fixture(scope="session")
def hazard_dataset(catalog: YamlCatalogRepository) -> DatasetDefinition:
    """The HU-001 dataset definition."""
    dataset = catalog.get("hazard_exposure")
    assert dataset is not None
    return dataset


@pytest.fixture(scope="session")
def reader(local_storage: LocalStorage) -> DuckDBTabularReader:
    """DuckDB reader over the synthetic tree."""
    return DuckDBTabularReader(
        local_storage, memory_limit="512MB", threads=2, query_timeout_seconds=30
    )


@pytest.fixture(scope="session")
def raster_reader(local_storage: LocalStorage) -> RasterioRasterReader:
    """Raster reader over the synthetic tree."""
    return RasterioRasterReader(local_storage, max_pixels=2_000_000)


@pytest.fixture(scope="session")
def raster_service(
    catalog: YamlCatalogRepository, raster_reader: RasterioRasterReader
) -> RasterDatasetService:
    """Generic raster service over the synthetic tree."""
    return RasterDatasetService(
        catalog, raster_reader, max_pixels=2_000_000, max_layers=100, max_points=50
    )


@pytest.fixture(scope="session")
def test_settings(storage_root: Path, catalog_path: Path) -> Settings:
    """Settings using the local backend and the synthetic tree."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="development",
        log_level="WARNING",
        storage_backend="local",
        local_storage_root=storage_root,
        catalog_path=catalog_path,
        partition_cache_ttl_seconds=0,
        duckdb_memory_limit="512MB",
        duckdb_threads=2,
        max_rows_per_response=500,
        default_page_size=50,
        raster_max_pixels=2_000_000,
        raster_max_layers=100,
        raster_max_points=50,
    )


@pytest.fixture(scope="session")
def client(test_settings: Settings) -> Iterator[TestClient]:
    """FastAPI test client with the lifespan executed."""
    application = create_app(test_settings)
    with TestClient(application) as test_client:
        yield test_client
