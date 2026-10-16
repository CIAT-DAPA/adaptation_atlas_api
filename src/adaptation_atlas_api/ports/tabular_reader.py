"""Port executing validated queries against tabular datasets."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adaptation_atlas_api.domain.models.query import TabularQuery
from adaptation_atlas_api.domain.models.results import TabularResult


class TabularReader(ABC):
    """Reads records from a tabular dataset according to a validated query."""

    @abstractmethod
    def query(self, query: TabularQuery) -> TabularResult:
        """Execute ``query`` and return the matching records.

        Args:
            query: A validated tabular query.

        Returns:
            The matching records and their metadata. A query that matches no source file
            or no record returns an empty result rather than raising.

        Raises:
            SourceUnavailableError: When the source files cannot be read.
            QueryExecutionError: When the engine fails to execute the query.
        """
