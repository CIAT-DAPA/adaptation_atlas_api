"""Catalog repository backed by one YAML file per dataset.

Every ``*.yaml`` / ``*.yml`` file inside the catalog directory is parsed and
validated against :class:`DatasetDefinition`. When a dataset enables
``metadata_sidecar`` and a storage adapter is supplied, the ``<file>.json``
sidecars found next to the Parquet files are merged into ``dataset.metadata``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from adaptation_atlas_api.domain.exceptions import CatalogConfigurationError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.ports.storage import Storage

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".parquet.json"


class YamlCatalogRepository(CatalogRepository):
    """Loads dataset definitions from YAML files in a directory."""

    def __init__(self, catalog_path: Path, storage: Storage | None = None) -> None:
        """Create the repository.

        Args:
            catalog_path: Directory containing the YAML dataset definitions.
            storage: Optional storage used to read metadata sidecars.
        """
        self._catalog_path = Path(catalog_path)
        self._storage = storage
        self._datasets: dict[str, DatasetDefinition] = {}

    def load(self) -> None:
        """Parse every YAML file and replace the in-memory registry.

        Raises:
            CatalogConfigurationError: If the directory is missing, a file is invalid, or
                two files declare the same dataset identifier.
        """
        # A missing catalog directory is a deployment error worth failing loudly on.
        if not self._catalog_path.is_dir():
            raise CatalogConfigurationError(
                f"Catalog directory '{self._catalog_path}' does not exist."
            )

        loaded: dict[str, DatasetDefinition] = {}
        files = sorted(
            list(self._catalog_path.glob("*.yaml")) + list(self._catalog_path.glob("*.yml"))
        )

        # Parse every definition file, failing on the first invalid one.
        for file_path in files:
            dataset = self._parse_file(file_path)
            # Duplicate identifiers would make lookups ambiguous.
            if dataset.id in loaded:
                raise CatalogConfigurationError(
                    f"Dataset id '{dataset.id}' is declared more than once in the catalog."
                )
            loaded[dataset.id] = self._enrich_with_sidecar(dataset)
            logger.info("Registered dataset '%s' from %s", dataset.id, file_path.name)

        self._datasets = loaded

    def list_all(self) -> list[DatasetDefinition]:
        """Return every registered dataset sorted by identifier.

        Returns:
            Dataset definitions sorted by ``id``.
        """
        return [self._datasets[key] for key in sorted(self._datasets)]

    def get(self, dataset_id: str) -> DatasetDefinition | None:
        """Return the dataset registered under ``dataset_id``.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The dataset definition or ``None``.
        """
        return self._datasets.get(dataset_id)

    @staticmethod
    def _parse_file(file_path: Path) -> DatasetDefinition:
        """Parse and validate one YAML dataset definition.

        Args:
            file_path: Path of the YAML file.

        Returns:
            The validated dataset definition.

        Raises:
            CatalogConfigurationError: If the YAML is malformed or fails validation.
        """
        try:
            raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise CatalogConfigurationError(
                f"Catalog file '{file_path.name}' is not valid YAML: {error}"
            ) from error

        # A dataset file must contain a mapping at the top level.
        if not isinstance(raw, dict):
            raise CatalogConfigurationError(
                f"Catalog file '{file_path.name}' must contain a mapping at the top level."
            )

        try:
            return DatasetDefinition.model_validate(raw)
        except ValidationError as error:
            raise CatalogConfigurationError(
                f"Catalog file '{file_path.name}' is invalid: {error}"
            ) from error

    def _enrich_with_sidecar(self, dataset: DatasetDefinition) -> DatasetDefinition:
        """Merge the first readable metadata sidecar into ``dataset.metadata``.

        Args:
            dataset: Dataset to enrich.

        Returns:
            The same dataset, or a copy whose ``metadata`` includes the sidecar content
            under the ``source_metadata`` key.
        """
        # Sidecar enrichment is optional and requires a storage adapter.
        if not dataset.storage.metadata_sidecar or self._storage is None:
            return dataset

        prefix = dataset.storage.static_prefix
        try:
            sidecars = self._storage.list_files(prefix, suffix=SIDECAR_SUFFIX)
        except Exception:  # noqa: BLE001 - enrichment must never break startup.
            logger.warning("Could not list metadata sidecars for '%s'", dataset.id, exc_info=True)
            return dataset

        # Without sidecars there is nothing to merge.
        if not sidecars:
            return dataset

        try:
            content = json.loads(self._storage.read_text(sorted(sidecars)[0]))
        except Exception:  # noqa: BLE001 - a broken sidecar must not break startup.
            logger.warning("Could not read metadata sidecar for '%s'", dataset.id, exc_info=True)
            return dataset

        metadata = dict(dataset.metadata)
        metadata["source_metadata"] = content
        return dataset.model_copy(update={"metadata": metadata})
