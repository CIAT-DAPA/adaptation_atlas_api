"""Unit tests for the generic tabular service using a fake reader (no DuckDB involved)."""

from __future__ import annotations

import pytest

from adaptation_atlas_api.records.application.tabular_service import TabularDatasetService
from adaptation_atlas_api.records.domain.query import OutputFormat, TabularQuery
from adaptation_atlas_api.records.ports.tabular_reader import TabularReader
from adaptation_atlas_api.shared.domain.exceptions import (
    DatasetNotFoundError,
    InvalidPaginationError,
    UnsupportedFormatError,
)
from adaptation_atlas_api.shared.domain.results import TabularResult
from adaptation_atlas_api.shared.infrastructure.yaml_catalog import YamlCatalogRepository


class FakeReader(TabularReader):
    """Records the query it receives and returns a canned result."""

    def __init__(self) -> None:
        self.last_query: TabularQuery | None = None

    def query(self, query: TabularQuery) -> TabularResult:
        self.last_query = query
        columns = [item.name for item in query.dataset.response_fields]
        return TabularResult.empty(
            query.dataset.id, columns, query.limit, query.offset, query.filters.applied
        )


@pytest.fixture
def fake_reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def service(catalog: YamlCatalogRepository, fake_reader: FakeReader) -> TabularDatasetService:
    return TabularDatasetService(catalog, fake_reader, max_rows=100, default_page_size=10)


def test_unknown_dataset_raises_not_found(service: TabularDatasetService) -> None:
    with pytest.raises(DatasetNotFoundError) as error:
        service.query("nope", {"country": "KEN", "period": "historic"})
    assert error.value.parameter == "dataset_id"
    assert "hazard_exposure" in (error.value.allowed_values or [])


def test_query_builds_validated_query_and_delegates(
    service: TabularDatasetService, fake_reader: FakeReader
) -> None:
    result = service.query(
        "hazard_exposure",
        {"country": "ken", "period": "historic", "crop": "maize"},
        output_format="json",
    )
    assert fake_reader.last_query is not None
    assert fake_reader.last_query.filters.column_filters["iso3"] == ["KEN"]
    assert fake_reader.last_query.limit == 10
    assert fake_reader.last_query.output_format is OutputFormat.JSON
    assert result.total_count == 0
    assert result.applied_filters["crop"] == "maize"


def test_service_is_dataset_agnostic(
    service: TabularDatasetService, fake_reader: FakeReader
) -> None:
    service.query("phenology_seasonal_test", {"country": "eth", "variant": "plus_rain"})
    assert fake_reader.last_query is not None
    assert fake_reader.last_query.filters.partition_values == {"country": ["ETH"]}
    assert fake_reader.last_query.filters.file_pattern_keys == ["plus_rain"]


def test_unsupported_format_is_rejected(service: TabularDatasetService) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        service.query(
            "hazard_exposure", {"country": "KEN", "period": "historic"}, output_format="xlsx"
        )
    assert error.value.allowed_values == ["json", "csv"]


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (10, -1)])
def test_pagination_bounds(service: TabularDatasetService, limit: int, offset: int) -> None:
    with pytest.raises(InvalidPaginationError):
        service.query(
            "hazard_exposure", {"country": "KEN", "period": "historic"}, limit=limit, offset=offset
        )
