"""Port exposing which partition combinations exist for a dataset."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition


class PartitionIndex(ABC):
    """Index of the partition combinations actually present in storage."""

    @abstractmethod
    def available_combinations(self, dataset: DatasetDefinition) -> list[dict[str, str]]:
        """Return every existing combination of partition values for ``dataset``.

        Args:
            dataset: Dataset whose storage is indexed.

        Returns:
            One dictionary per existing leaf folder, mapping partition key to value and
            including the ``file_pattern`` keys found inside it under ``"file_pattern"``.
        """

    @abstractmethod
    def available_values(self, dataset: DatasetDefinition, partition: str) -> list[str]:
        """Return the distinct values found for one partition key.

        Args:
            dataset: Dataset whose storage is indexed.
            partition: Partition key.

        Returns:
            Sorted distinct values.
        """

    @abstractmethod
    def refresh(self, dataset: DatasetDefinition) -> None:
        """Force the index of ``dataset`` to be rebuilt from storage.

        Args:
            dataset: Dataset whose index is rebuilt.
        """
