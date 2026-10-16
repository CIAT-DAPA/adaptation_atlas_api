"""HU-001: retrieve hazard exposure data for a selected territory."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from adaptation_atlas_api.domain.exceptions import (
    DatasetNotFoundError,
    InvalidPaginationError,
    UnsupportedFormatError,
)
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition, DatasetType
from adaptation_atlas_api.domain.models.query import OutputFormat, TabularQuery
from adaptation_atlas_api.domain.models.results import TabularResult
from adaptation_atlas_api.domain.validation.filters import validate_filters
from adaptation_atlas_api.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.ports.tabular_reader import TabularReader

logger = logging.getLogger(__name__)

# Query parameters that control the response and are never dataset filters.
CONTROL_PARAMETERS = {"format", "limit", "offset"}


class HazardExposureService:
    """Use case behind ``GET /hazard-exposure/{dataset_id}``.

    The service resolves the dataset in the catalog, validates every supplied filter
    against the registered vocabularies, builds a :class:`TabularQuery` and delegates
    its execution to the tabular reader port.
    """

    def __init__(
        self,
        catalog: CatalogRepository,
        reader: TabularReader,
        *,
        max_rows: int,
        default_page_size: int,
    ) -> None:
        """Create the service.

        Args:
            catalog: Repository holding the dataset definitions.
            reader: Adapter able to execute tabular queries.
            max_rows: Hard cap on the number of rows returned in one response.
            default_page_size: Page size used when the client omits ``limit``.
        """
        self._catalog = catalog
        self._reader = reader
        self._max_rows = max_rows
        self._default_page_size = default_page_size

    def resolve_dataset(self, dataset_id: str) -> DatasetDefinition:
        """Find a tabular dataset by its public identifier.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The dataset definition.

        Raises:
            DatasetNotFoundError: If the identifier is unknown or not a tabular dataset.
        """
        dataset = self._catalog.get(dataset_id)

        # Unknown identifiers and non-tabular datasets are both reported as not found,
        # listing only the tabular datasets this endpoint can serve.
        if dataset is None or dataset.type is not DatasetType.TABULAR:
            tabular_ids = [
                item.id for item in self._catalog.list_all() if item.type is DatasetType.TABULAR
            ]
            raise DatasetNotFoundError(
                f"Dataset '{dataset_id}' is not a registered tabular dataset.",
                parameter="dataset_id",
                allowed_values=tabular_ids,
            )
        return dataset

    def build_query(
        self,
        dataset_id: str,
        raw_filters: Mapping[str, str | None],
        *,
        output_format: str = "json",
        limit: int | None = None,
        offset: int = 0,
    ) -> TabularQuery:
        """Validate the request and build the query to execute.

        Args:
            dataset_id: Public dataset identifier.
            raw_filters: Every query parameter received, including control parameters.
            output_format: Requested format (``json`` or ``csv``).
            limit: Requested page size; ``None`` applies the default page size.
            offset: Number of rows to skip.

        Returns:
            A validated :class:`TabularQuery`.

        Raises:
            DatasetNotFoundError: If the dataset is unknown.
            UnsupportedFormatError: If the format is not supported by the dataset.
            InvalidPaginationError: If ``limit`` or ``offset`` are out of range.
            UnsupportedFilterError: If a parameter is not registered.
            MissingRequiredFilterError: If a required filter is absent.
            InvalidFilterValueError: If a value is outside its vocabulary.
        """
        dataset = self.resolve_dataset(dataset_id)
        resolved_format = self._resolve_format(dataset, output_format)
        resolved_limit, resolved_offset = self._resolve_pagination(limit, offset)
        validated = validate_filters(dataset, raw_filters, reserved_parameters=CONTROL_PARAMETERS)

        return TabularQuery(
            dataset=dataset,
            filters=validated,
            limit=resolved_limit,
            offset=resolved_offset,
            output_format=resolved_format,
        )

    def query(
        self,
        dataset_id: str,
        raw_filters: Mapping[str, str | None],
        *,
        output_format: str = "json",
        limit: int | None = None,
        offset: int = 0,
    ) -> TabularResult:
        """Validate the request, execute it and return the matching records.

        Args:
            dataset_id: Public dataset identifier.
            raw_filters: Every query parameter received, including control parameters.
            output_format: Requested format (``json`` or ``csv``).
            limit: Requested page size; ``None`` applies the default page size.
            offset: Number of rows to skip.

        Returns:
            The matching records. No match yields an empty successful result.
        """
        query = self.build_query(
            dataset_id,
            raw_filters,
            output_format=output_format,
            limit=limit,
            offset=offset,
        )
        logger.info(
            "Executing hazard exposure query dataset=%s filters=%s limit=%s offset=%s",
            dataset_id,
            query.filters.applied,
            query.limit,
            query.offset,
        )
        return self._reader.query(query)

    @staticmethod
    def _resolve_format(dataset: DatasetDefinition, output_format: str) -> OutputFormat:
        """Validate the requested output format against the dataset capabilities.

        Args:
            dataset: Dataset being queried.
            output_format: Requested format name.

        Returns:
            The matching :class:`OutputFormat`.

        Raises:
            UnsupportedFormatError: If the format is unknown or not enabled for the dataset.
        """
        normalized = (output_format or "json").strip().lower()

        # The format must be both a known enum member and enabled for the dataset.
        if normalized not in {item.value for item in OutputFormat} or (
            normalized not in dataset.output_formats
        ):
            raise UnsupportedFormatError(
                f"Format '{output_format}' is not supported by dataset '{dataset.id}'.",
                parameter="format",
                allowed_values=dataset.output_formats,
            )
        return OutputFormat(normalized)

    def _resolve_pagination(self, limit: int | None, offset: int) -> tuple[int, int]:
        """Apply defaults and bounds to the pagination parameters.

        Args:
            limit: Requested page size or ``None``.
            offset: Requested offset.

        Returns:
            The effective ``(limit, offset)`` pair.

        Raises:
            InvalidPaginationError: If ``limit`` is not positive, exceeds the configured
                maximum, or ``offset`` is negative.
        """
        effective_limit = self._default_page_size if limit is None else limit

        # A page size must be at least one row.
        if effective_limit < 1:
            raise InvalidPaginationError(
                "Parameter 'limit' must be greater than zero.", parameter="limit"
            )
        # The hard cap protects the service from oversized responses.
        if effective_limit > self._max_rows:
            raise InvalidPaginationError(
                f"Parameter 'limit' cannot exceed {self._max_rows}.", parameter="limit"
            )
        # Negative offsets have no meaning.
        if offset < 0:
            raise InvalidPaginationError(
                "Parameter 'offset' cannot be negative.", parameter="offset"
            )
        return effective_limit, offset
