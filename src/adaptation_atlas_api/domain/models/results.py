"""Models describing the outcome of a tabular query."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TabularResult:
    """Records returned by a tabular query together with response metadata.

    Attributes:
        dataset_id: Public identifier of the dataset queried.
        columns: Ordered public field names present in every record.
        records: Matching rows as dictionaries keyed by ``columns``.
        total_count: Total number of matching rows before pagination.
        limit: Page size applied to the query.
        offset: Number of rows skipped.
        applied_filters: Public filter name -> canonical value or values actually applied.
        source_files: Number of source files that matched the query.
    """

    dataset_id: str
    columns: list[str]
    records: list[dict[str, Any]]
    total_count: int
    limit: int
    offset: int = 0
    applied_filters: dict[str, str | list[str]] = field(default_factory=dict)
    source_files: int = 0

    @property
    def returned_count(self) -> int:
        """Return the number of records in this page.

        Returns:
            Length of ``records``.
        """
        return len(self.records)

    @property
    def has_more(self) -> bool:
        """Tell whether more records exist beyond this page.

        Returns:
            ``True`` when ``offset + returned_count`` is lower than ``total_count``.
        """
        return self.offset + self.returned_count < self.total_count

    @classmethod
    def empty(
        cls,
        dataset_id: str,
        columns: list[str],
        limit: int,
        offset: int = 0,
        applied_filters: dict[str, str | list[str]] | None = None,
    ) -> TabularResult:
        """Build a successful empty result (no matching records).

        Args:
            dataset_id: Public identifier of the dataset queried.
            columns: Public field names the response would contain.
            limit: Page size applied to the query.
            offset: Number of rows skipped.
            applied_filters: Filters that were applied.

        Returns:
            A :class:`TabularResult` with no records and ``total_count`` of zero.
        """
        return cls(
            dataset_id=dataset_id,
            columns=columns,
            records=[],
            total_count=0,
            limit=limit,
            offset=offset,
            applied_filters=dict(applied_filters or {}),
            source_files=0,
        )
