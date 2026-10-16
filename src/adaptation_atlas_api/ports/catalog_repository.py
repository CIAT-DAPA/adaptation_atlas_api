"""Port giving access to the registered dataset definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adaptation_atlas_api.domain.models.dataset import DatasetDefinition


class CatalogRepository(ABC):
    """Read access to the dataset catalog."""

    @abstractmethod
    def load(self) -> None:
        """Load (or reload) every dataset definition from its backing store."""

    @abstractmethod
    def list_all(self) -> list[DatasetDefinition]:
        """Return every registered dataset.

        Returns:
            Dataset definitions sorted by identifier.
        """

    @abstractmethod
    def get(self, dataset_id: str) -> DatasetDefinition | None:
        """Return the dataset registered under ``dataset_id``.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The dataset definition, or ``None`` when it is not registered.
        """
