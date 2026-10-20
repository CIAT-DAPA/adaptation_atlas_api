"""Port turning a tabular result into an export representation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from adaptation_atlas_api.shared.domain.results import TabularResult


class TabularExporter(ABC):
    """Serializes a :class:`TabularResult` into a streamable text representation."""

    media_type: str = "text/plain"
    file_extension: str = "txt"

    @abstractmethod
    def export(self, result: TabularResult) -> Iterator[str]:
        """Serialize ``result`` chunk by chunk.

        Args:
            result: The result to serialize.

        Returns:
            An iterator of text chunks forming the complete document.
        """
