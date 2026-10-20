"""Dataset discovery use cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition, DatasetType
from adaptation_atlas_api.shared.domain.exceptions import (
    DatasetNotFoundError,
    InvalidFilterValueError,
)
from adaptation_atlas_api.shared.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.shared.ports.partition_index import PartitionIndex

logger = logging.getLogger(__name__)


@dataclass
class DatasetAvailability:
    """A dataset definition together with what actually exists in storage.

    Attributes:
        dataset: The registered definition.
        available_partition_values: Partition key -> values found in storage.
        available_combinations: Existing combinations of partition values.
        example_query: Relative URL of a valid records query (tabular datasets) or of the
            raster endpoint (raster datasets).
        example_filters: The same required filters as a JSON-ready object.
        example_request: Example request body for raster datasets (empty for tabular).
    """

    dataset: DatasetDefinition
    available_partition_values: dict[str, list[str]] = field(default_factory=dict)
    available_combinations: list[dict[str, str]] = field(default_factory=list)
    example_query: str = ""
    example_filters: dict[str, str] = field(default_factory=dict)
    example_request: dict[str, Any] = field(default_factory=dict)


def build_example_filters(dataset: DatasetDefinition) -> dict[str, str]:
    """Build an example value for every required filter of ``dataset``.

    Every required filter is given its catalog ``example`` value, or the first allowed
    value when no example is declared.

    Args:
        dataset: Dataset to build the example for.

    Returns:
        Filter name -> example value, in catalog order.
    """
    examples: dict[str, str] = {}

    # Only required filters are needed for a valid query; optional ones are omitted.
    for filter_definition in dataset.required_filters:
        value = filter_definition.example
        # Without an explicit example fall back to the first vocabulary entry.
        if value is None and filter_definition.allowed_values:
            value = filter_definition.allowed_values[0]
        # Filters with neither example nor vocabulary get a placeholder the user replaces.
        if value is None:
            value = f"<{filter_definition.name}>"
        examples[filter_definition.name] = str(value)
    return examples


def build_example_query(dataset: DatasetDefinition, api_prefix: str = "/api/v1") -> str:
    """Build a copy-ready URL for the endpoint that serves ``dataset``.

    Tabular datasets get a records URL with their required filters; raster datasets get
    the URL of the raster endpoint (its parameters travel in the request body, see
    :func:`build_example_request`).

    Args:
        dataset: Dataset to build the example for.
        api_prefix: URL prefix under which the API is mounted.

    Returns:
        A relative URL such as ``/api/v1/datasets/x/records?country=KEN&period=historic``.
    """
    # Raster datasets are queried with POST, so the example is the endpoint itself.
    if dataset.is_raster:
        return f"{api_prefix}/datasets/{dataset.id}/raster"
    pairs = [
        f"{name}={quote(value, safe='')}" for name, value in build_example_filters(dataset).items()
    ]
    query = "&".join(pairs)
    url = f"{api_prefix}/datasets/{dataset.id}/records"
    return f"{url}?{query}" if query else url


def build_example_request(dataset: DatasetDefinition) -> dict[str, Any]:
    """Build an example body for ``POST /datasets/{id}/raster``.

    Args:
        dataset: Raster dataset to build the example for.

    Returns:
        A JSON-ready request with the required filters, a small bounding box inside the
        coverage, a one-year period and a calendar-month aggregation.
    """
    raster = dataset.raster
    # Tabular datasets have no raster request.
    if raster is None:
        return {}
    coverage = raster.coverage or [-180.0, -90.0, 180.0, 90.0]
    centre_x = (coverage[0] + coverage[2]) / 2
    centre_y = (coverage[1] + coverage[3]) / 2
    half = max(raster.resolution * 10, 0.5)
    example: dict[str, Any] = {
        "filters": build_example_filters(dataset),
        "bbox": [
            round(centre_x - half, 3),
            round(centre_y - half, 3),
            round(centre_x + half, 3),
            round(centre_y + half, 3),
        ],
        "format": "geotiff",
    }
    # Datasets with a time range need a period; one year is a reasonable example.
    if raster.time_range_required:
        example["start_date"] = "2020-01-01"
        example["end_date"] = "2020-12-31"
        example["aggregation"] = {"type": "calendar_month"}
    return example


class CatalogService:
    """Lists registered datasets and describes them to API consumers."""

    def __init__(
        self,
        catalog: CatalogRepository,
        partition_index: PartitionIndex | None = None,
        api_prefix: str = "/api/v1",
    ) -> None:
        """Create the service.

        Args:
            catalog: Repository holding the dataset definitions.
            partition_index: Optional index used to report existing partition combinations.
            api_prefix: URL prefix used to build example queries.
        """
        self._catalog = catalog
        self._partition_index = partition_index
        self._api_prefix = api_prefix

    def list_datasets(
        self, domain: str | None = None, dataset_type: str | None = None
    ) -> list[DatasetDefinition]:
        """Return the registered datasets, optionally restricted by domain and type.

        Args:
            domain: Thematic domain to filter by (case insensitive), or ``None``.
            dataset_type: ``tabular`` or ``raster`` to keep one kind, or ``None``.

        Returns:
            Dataset definitions sorted by identifier.

        Raises:
            InvalidFilterValueError: If ``dataset_type`` is not a known kind.
        """
        datasets = self._catalog.list_all()

        # The domain filter keeps datasets of one thematic domain.
        if domain is not None and domain.strip():
            wanted = domain.strip().lower()
            datasets = [dataset for dataset in datasets if dataset.domain.lower() == wanted]
        # The type filter keeps tabular or raster datasets only.
        if dataset_type is not None and dataset_type.strip():
            normalized = dataset_type.strip().lower()
            # Unknown kinds are a client error listing the valid ones.
            if normalized not in {item.value for item in DatasetType}:
                raise InvalidFilterValueError(
                    f"Invalid value '{dataset_type}' for parameter 'type'.",
                    parameter="type",
                    allowed_values=[item.value for item in DatasetType],
                )
            datasets = [dataset for dataset in datasets if dataset.type.value == normalized]
        return datasets

    def get_dataset(self, dataset_id: str) -> DatasetDefinition:
        """Return the dataset registered under ``dataset_id``.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The dataset definition.

        Raises:
            DatasetNotFoundError: If the identifier is not registered.
        """
        dataset = self._catalog.get(dataset_id)

        # An unknown identifier is a client error that must name the parameter.
        if dataset is None:
            raise DatasetNotFoundError(
                f"Dataset '{dataset_id}' is not registered.",
                parameter="dataset_id",
                allowed_values=[item.id for item in self._catalog.list_all()],
            )
        return dataset

    def describe_dataset(self, dataset_id: str) -> DatasetAvailability:
        """Return a dataset with the partition combinations found in storage.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The definition enriched with availability information when discovery is enabled.

        Raises:
            DatasetNotFoundError: If the identifier is not registered.
        """
        dataset = self.get_dataset(dataset_id)
        availability = DatasetAvailability(
            dataset=dataset,
            example_query=build_example_query(dataset, self._api_prefix),
            example_filters=build_example_filters(dataset),
            example_request=build_example_request(dataset),
        )

        # Availability is only computed when discovery is enabled and an index is wired.
        if not dataset.storage.discover_partitions or self._partition_index is None:
            return availability

        try:
            availability.available_combinations = self._partition_index.available_combinations(
                dataset
            )
            # Collect the distinct values of every declared partition key.
            for partition in dataset.partitions:
                availability.available_partition_values[partition.name] = (
                    self._partition_index.available_values(dataset, partition.name)
                )
        except Exception:  # noqa: BLE001 - availability is best effort, never fatal.
            # Discovery failures must not break dataset description; log and continue.
            logger.warning("Partition discovery failed for dataset '%s'", dataset_id, exc_info=True)
        return availability
