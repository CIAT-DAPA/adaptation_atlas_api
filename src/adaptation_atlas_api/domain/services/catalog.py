"""Dataset discovery use cases."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from adaptation_atlas_api.domain.exceptions import DatasetNotFoundError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.ports.partition_index import PartitionIndex

logger = logging.getLogger(__name__)


@dataclass
class DatasetAvailability:
    """A dataset definition together with what actually exists in storage.

    Attributes:
        dataset: The registered definition.
        available_partition_values: Partition key -> values found in storage.
        available_combinations: Existing combinations of partition values.
    """

    dataset: DatasetDefinition
    available_partition_values: dict[str, list[str]] = field(default_factory=dict)
    available_combinations: list[dict[str, str]] = field(default_factory=list)


class CatalogService:
    """Lists registered datasets and describes them to API consumers."""

    def __init__(
        self,
        catalog: CatalogRepository,
        partition_index: PartitionIndex | None = None,
    ) -> None:
        """Create the service.

        Args:
            catalog: Repository holding the dataset definitions.
            partition_index: Optional index used to report existing partition combinations.
        """
        self._catalog = catalog
        self._partition_index = partition_index

    def list_datasets(self, domain: str | None = None) -> list[DatasetDefinition]:
        """Return the registered datasets, optionally restricted to one domain.

        Args:
            domain: Thematic domain to filter by (case insensitive), or ``None``.

        Returns:
            Dataset definitions sorted by identifier.
        """
        datasets = self._catalog.list_all()

        # Without a domain filter the whole catalog is returned.
        if domain is None or not domain.strip():
            return datasets
        wanted = domain.strip().lower()
        return [dataset for dataset in datasets if dataset.domain.lower() == wanted]

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
        availability = DatasetAvailability(dataset=dataset)

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
