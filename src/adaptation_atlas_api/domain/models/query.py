"""Models describing a validated query against a tabular dataset."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from adaptation_atlas_api.domain.models.dataset import DatasetDefinition


class OutputFormat(str, Enum):
    """Response formats supported by tabular endpoints."""

    JSON = "json"
    CSV = "csv"


class PredicateOperator(str, Enum):
    """Comparison operators a storage adapter must support for predicates."""

    EQUALS = "eq"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


@dataclass(frozen=True)
class Predicate:
    """A storage-agnostic condition on a single column.

    Attributes:
        column: Column the condition applies to.
        operator: Comparison operator.
        value: Comparison value (ignored for null checks).
    """

    column: str
    operator: PredicateOperator
    value: str | None = None


@dataclass(frozen=True)
class DerivedCondition:
    """Condition produced by a derived filter, as alternatives combined with OR.

    Attributes:
        name: Public filter name.
        alternatives: One list of predicates (combined with AND) per requested value.
    """

    name: str
    alternatives: list[list[Predicate]] = field(default_factory=list)


@dataclass(frozen=True)
class ValidatedFilters:
    """Filters already checked against the catalog, grouped by resolution strategy.

    Every filter may carry several values; values of the same filter are combined with
    OR and different filters with AND.

    Attributes:
        partition_values: Partition key -> values used to build the storage paths.
        column_filters: Column name -> values applied as ``WHERE column IN (...)``.
        derived_conditions: Conditions produced by derived rules (e.g. ``admin_level``).
        file_pattern_keys: Keys of ``storage.file_patterns`` selecting the files to read.
        applied: Public filter name -> canonical value or values, for response metadata.
        filter_names_by_column: Column name -> public filter name, used to report errors
            with the parameter the client actually sent.
    """

    partition_values: dict[str, list[str]] = field(default_factory=dict)
    column_filters: dict[str, list[str]] = field(default_factory=dict)
    derived_conditions: list[DerivedCondition] = field(default_factory=list)
    file_pattern_keys: list[str] = field(default_factory=lambda: ["default"])
    applied: dict[str, str | list[str]] = field(default_factory=dict)
    filter_names_by_column: dict[str, str] = field(default_factory=dict)


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
