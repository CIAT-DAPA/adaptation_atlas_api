"""Models describing a validated query against a tabular dataset."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition
from adaptation_atlas_api.shared.domain.filters import ValidatedFilters


class OutputFormat(str, Enum):
    """Response formats supported by tabular endpoints."""

    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class TabularQuery:
    """A fully validated request ready to be executed by a tabular reader.

    Attributes:
        dataset: Definition of the dataset being queried.
        filters: Validated filters grouped by resolution strategy.
        limit: Maximum number of records to return.
        offset: Number of records to skip.
        output_format: Requested response format.
    """

    dataset: DatasetDefinition
    filters: ValidatedFilters
    limit: int
    offset: int = 0
    output_format: OutputFormat = OutputFormat.JSON
