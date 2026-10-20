"""Partition index built by listing the storage and parsing Hive style paths."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from adaptation_atlas_api.domain.models.dataset import DatasetDefinition, extract_placeholders
from adaptation_atlas_api.ports.partition_index import PartitionIndex
from adaptation_atlas_api.ports.storage import Storage

logger = logging.getLogger(__name__)


@dataclass
class _IndexEntry:
    """Cached combinations of one dataset.

    Attributes:
        built_at: Monotonic timestamp of the last build.
        combinations: Existing partition combinations.
    """

    built_at: float
    combinations: list[dict[str, str]] = field(default_factory=list)


class StoragePartitionIndex(PartitionIndex):
    """Discovers existing partition combinations by listing Parquet files in storage.

    The listing is cached per dataset for ``ttl_seconds`` so the storage is not
    scanned on every request.
    """

    def __init__(self, storage: Storage, ttl_seconds: int = 3600) -> None:
        """Create the index.

        Args:
            storage: Storage adapter used to list files.
            ttl_seconds: Lifetime of a cached listing; ``0`` disables caching.
        """
        self._storage = storage
        self._ttl = ttl_seconds
        self._cache: dict[str, _IndexEntry] = {}
        self._lock = threading.Lock()

    def available_combinations(self, dataset: DatasetDefinition) -> list[dict[str, str]]:
        """Return the existing partition combinations, rebuilding the cache when stale.

        Args:
            dataset: Dataset whose storage is listed.

        Returns:
            One dictionary per leaf folder with partition values and ``file_pattern`` keys.
        """
        with self._lock:
            entry = self._cache.get(dataset.id)

            # Rebuild the listing when it is missing or older than the TTL.
            if entry is None or (time.monotonic() - entry.built_at) > self._ttl:
                entry = _IndexEntry(built_at=time.monotonic(), combinations=self._build(dataset))
                self._cache[dataset.id] = entry
            return [dict(item) for item in entry.combinations]

    def available_values(self, dataset: DatasetDefinition, partition: str) -> list[str]:
        """Return the sorted distinct values of ``partition`` found in storage.

        Args:
            dataset: Dataset whose storage is listed.
            partition: Partition key.

        Returns:
            Sorted distinct values.
        """
        values = {
            combination[partition]
            for combination in self.available_combinations(dataset)
            if partition in combination
        }
        return sorted(values)

    def refresh(self, dataset: DatasetDefinition) -> None:
        """Drop the cached listing of ``dataset`` so it is rebuilt on next access.

        Args:
            dataset: Dataset whose cache entry is dropped.
        """
        with self._lock:
            self._cache.pop(dataset.id, None)

    def _build(self, dataset: DatasetDefinition) -> list[dict[str, str]]:
        """List the dataset prefix and parse every Parquet key into partition values.

        Args:
            dataset: Dataset whose storage is listed.

        Returns:
            Existing combinations, one per leaf folder, sorted deterministically.
        """
        prefix = dataset.storage.static_prefix
        partition_names = {partition.name for partition in dataset.partitions}
        suffix = listing_suffix(dataset)
        keys = self._storage.list_files(prefix, suffix=suffix)
        logger.info("Partition index for '%s': %d %s files listed", dataset.id, len(keys), suffix)

        combinations: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}

        # Parse each key into its partition values and the file pattern it belongs to.
        for key in keys:
            folder, _, file_name = key.rpartition("/")
            values: dict[str, str] = {}

            # Extract ``name=value`` segments that correspond to declared partitions.
            for segment in folder.split("/"):
                name, separator, value = segment.partition("=")
                # Segments without ``=`` or with undeclared keys are not partitions.
                if separator and name in partition_names:
                    values[name] = value

            match = self._match_pattern(dataset, file_name)
            # Files that match no registered pattern are ignored.
            if match is None:
                continue
            pattern_key, name_values = match
            # Partition values embedded in the file name complement the folder ones; a
            # value already read from the folder path is authoritative.
            for name, value in name_values.items():
                values.setdefault(name, value)

            identity = tuple(sorted(values.items()))
            combination = combinations.setdefault(identity, dict(values))
            patterns = combination.get("file_patterns", "")
            # Accumulate every pattern key found in the same leaf folder.
            if pattern_key not in patterns.split(","):
                combination["file_patterns"] = (
                    f"{patterns},{pattern_key}" if patterns else pattern_key
                )

        return [combinations[identity] for identity in sorted(combinations)]

    @staticmethod
    def _match_pattern(
        dataset: DatasetDefinition, file_name: str
    ) -> tuple[str, dict[str, str]] | None:
        """Find the file pattern matching ``file_name`` and the partition values it embeds.

        Args:
            dataset: Dataset declaring the file patterns.
            file_name: Base name of the file.

        Returns:
            The matching pattern key and the ``{placeholder}`` values captured from the
            name, or ``None`` when no pattern matches.
        """
        # Patterns are checked in catalog order so more specific ones can be listed first.
        for key, glob in dataset.storage.file_patterns.items():
            match = pattern_to_regex(glob).fullmatch(file_name)
            # The first matching pattern wins.
            if match is not None:
                return key, {name: value for name, value in match.groupdict().items() if value}
        return None


def listing_suffix(dataset: DatasetDefinition) -> str:
    """Return the file extension (with dot) to list for a dataset.

    The extension is taken from the default file pattern (``*.parquet`` -> ``.parquet``,
    ``{variable}-*.tif`` -> ``.tif``) so tabular and raster datasets are listed alike.

    Args:
        dataset: Dataset whose files are listed.

    Returns:
        The extension including the leading dot, or an empty string when the pattern has
        no literal extension.
    """
    pattern = dataset.storage.file_patterns[dataset.storage.default_file_pattern]
    _, dot, extension = pattern.rpartition(".")
    # Only a literal extension (no wildcard or placeholder) can be used as a suffix.
    if not dot or any(token in extension for token in ("*", "?", "{", "}")):
        return ""
    return "." + extension


def pattern_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a file pattern with ``*``, ``?`` and ``{placeholder}`` into a regex.

    Args:
        glob: File pattern as declared in the catalog.

    Returns:
        A compiled regular expression with one named group per placeholder.
    """
    placeholders = extract_placeholders(glob)
    pieces: list[str] = []
    rest = glob

    # Split the pattern on its placeholders, escaping the literal pieces in between.
    for placeholder in placeholders:
        literal, _, rest = rest.partition("{" + placeholder + "}")
        pieces.append(_glob_to_regex(literal))
        pieces.append(f"(?P<{placeholder}>[^/]+)")
    pieces.append(_glob_to_regex(rest))
    return re.compile("".join(pieces))


def _glob_to_regex(literal: str) -> str:
    """Escape a glob fragment, translating ``*`` and ``?`` wildcards.

    Args:
        literal: Glob fragment without placeholders.

    Returns:
        The regex fragment.
    """
    return re.escape(literal).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
