"""Port abstracting where dataset files live (S3, local filesystem...)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Storage(ABC):
    """Resolves dataset paths and configures query engines for a storage backend."""

    @abstractmethod
    def resolve_uri(self, relative_path: str) -> str:
        """Translate a path relative to the storage root into an engine readable URI.

        Args:
            relative_path: Path (or glob) relative to the storage root.

        Returns:
            A URI the query engine can open (``s3://bucket/...`` or a local path).
        """

    @abstractmethod
    def configure_connection(self, connection: Any) -> None:
        """Install extensions and credentials required by a DuckDB connection.

        Args:
            connection: An open DuckDB connection.
        """

    @abstractmethod
    def list_files(self, prefix: str, suffix: str = "") -> list[str]:
        """List files under a prefix, relative to the storage root.

        Args:
            prefix: Directory prefix relative to the storage root.
            suffix: Optional suffix the returned keys must end with (e.g. ``.parquet``).

        Returns:
            Relative paths of the matching files.
        """

    @abstractmethod
    def read_text(self, relative_path: str) -> str:
        """Read a small text object (e.g. a JSON metadata sidecar).

        Args:
            relative_path: Path relative to the storage root.

        Returns:
            The file content decoded as UTF-8.
        """
