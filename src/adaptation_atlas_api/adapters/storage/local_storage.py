"""Storage adapter for datasets stored on the local filesystem (tests and development)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaptation_atlas_api.ports.storage import Storage


class LocalStorage(Storage):
    """Resolves paths under a local root directory that mirrors the bucket layout."""

    def __init__(self, root: Path) -> None:
        """Create the adapter.

        Args:
            root: Directory that plays the role of the bucket root.
        """
        self._root = Path(root)

    @property
    def root(self) -> Path:
        """Return the root directory.

        Returns:
            The configured root path.
        """
        return self._root

    def resolve_uri(self, relative_path: str) -> str:
        """Join ``relative_path`` to the root directory.

        Args:
            relative_path: Path or glob relative to the root.

        Returns:
            The absolute path as a POSIX string (DuckDB accepts it on every platform).
        """
        return (self._root / relative_path.lstrip("/")).as_posix()

    def configure_connection(self, connection: Any) -> None:
        """Nothing to configure for local files.

        Args:
            connection: An open DuckDB connection (unused).
        """
        return None

    def list_files(self, prefix: str, suffix: str = "") -> list[str]:
        """List files under ``prefix`` recursively.

        Args:
            prefix: Directory relative to the root.
            suffix: Optional suffix the file names must end with.

        Returns:
            Matching paths relative to the root, using forward slashes.
        """
        base = self._root / prefix.lstrip("/")

        # A prefix that does not exist simply has no files.
        if not base.exists():
            return []

        results: list[str] = []
        # Walk the whole subtree collecting regular files that match the suffix.
        for path in base.rglob("*"):
            # Directories and non-matching suffixes are skipped.
            if not path.is_file() or (suffix and not path.name.endswith(suffix)):
                continue
            results.append(path.relative_to(self._root).as_posix())
        return sorted(results)

    def read_text(self, relative_path: str) -> str:
        """Read a local text file.

        Args:
            relative_path: Path relative to the root.

        Returns:
            The file content decoded as UTF-8.
        """
        return (self._root / relative_path.lstrip("/")).read_text(encoding="utf-8")
