"""Validation of request filters against a dataset definition.

The validator enforces the exception flows of HU-001:

* an unregistered parameter produces ``UnsupportedFilterError``;
* a missing required parameter produces ``MissingRequiredFilterError``;
* a value outside the controlled vocabulary produces ``InvalidFilterValueError``
  carrying the valid values, and no query is executed.
"""

from __future__ import annotations

from collections.abc import Mapping

from adaptation_atlas_api.domain.exceptions import (
    CatalogConfigurationError,
    InvalidFilterValueError,
    MissingRequiredFilterError,
    UnsupportedFilterError,
)
from adaptation_atlas_api.domain.models.dataset import (
    DatasetDefinition,
    FilterDefinition,
    FilterSource,
)
from adaptation_atlas_api.domain.models.query import DerivedCondition, ValidatedFilters
from adaptation_atlas_api.domain.validation.derived import resolve_derived_rule


def normalize_value(filter_definition: FilterDefinition, raw_value: str) -> str:
    """Check a raw value against the filter vocabulary and return its canonical form.

    Args:
        filter_definition: Filter the value belongs to.
        raw_value: Value as supplied by the client.

    Returns:
        The canonical value (spelling taken from ``allowed_values`` when the filter is
        case insensitive), stripped of surrounding whitespace.

    Raises:
        InvalidFilterValueError: If the filter has a vocabulary and the value is not in it.
    """
    value = raw_value.strip()
    allowed = filter_definition.allowed_values

    # Filters without a vocabulary accept any non-empty value.
    if allowed is None:
        # An empty value cannot be matched against anything meaningful.
        if not value:
            raise InvalidFilterValueError(
                f"Parameter '{filter_definition.name}' cannot be empty.",
                parameter=filter_definition.name,
            )
        return value

    # Case-insensitive filters map the value to the canonical spelling of the vocabulary.
    if filter_definition.case_insensitive:
        lookup = {candidate.lower(): candidate for candidate in allowed}
        canonical = lookup.get(value.lower())
        # A value absent from the lowercase lookup is outside the vocabulary.
        if canonical is None:
            raise InvalidFilterValueError(
                f"Invalid value '{raw_value}' for parameter '{filter_definition.name}'.",
                parameter=filter_definition.name,
                allowed_values=allowed,
            )
        return canonical

    # Case-sensitive filters require an exact match.
    if value not in allowed:
        raise InvalidFilterValueError(
            f"Invalid value '{raw_value}' for parameter '{filter_definition.name}'.",
            parameter=filter_definition.name,
            allowed_values=allowed,
        )
    return value


RawValue = str | list[str] | None
VALUE_SEPARATOR = ","


def split_values(filter_definition: FilterDefinition, raw_value: str | list[str]) -> list[str]:
    """Turn the raw value(s) of a filter into a de-duplicated list of non-empty strings.

    Args:
        filter_definition: Filter the values belong to.
        raw_value: A single string (possibly comma separated) or a list of strings.

    Returns:
        The distinct values in order of appearance, surrounding whitespace removed.

    Raises:
        InvalidFilterValueError: If several values are given to a single-valued filter.
    """
    items = [raw_value] if isinstance(raw_value, str) else list(raw_value)
    values: list[str] = []

    # Expand comma separated entries and drop blanks while keeping the original order.
    for item in items:
        pieces = item.split(VALUE_SEPARATOR) if filter_definition.multiple else [item]
        for piece in pieces:
            cleaned = piece.strip()
            # Blank pieces (``a,,b``) and repeated values are ignored.
            if cleaned and cleaned not in values:
                values.append(cleaned)

    # Single-valued filters refuse lists outright.
    if len(values) > 1 and not filter_definition.multiple:
        raise InvalidFilterValueError(
            f"Parameter '{filter_definition.name}' accepts a single value.",
            parameter=filter_definition.name,
            allowed_values=filter_definition.allowed_values,
        )
    return values


def validate_filters(
    dataset: DatasetDefinition,
    raw_filters: Mapping[str, RawValue],
    reserved_parameters: set[str] | None = None,
) -> ValidatedFilters:
    """Validate the filters supplied for ``dataset`` and group them by resolution strategy.

    Args:
        dataset: Definition the filters are validated against.
        raw_filters: Every query parameter received, including unknown ones, mapped to its
            raw value: a string (comma separated for several values), a list of strings,
            or ``None`` when the parameter was sent without a value.
        reserved_parameters: Control parameters (``format``, ``limit``...) that are not
            dataset filters and must be ignored by the validator.

    Returns:
        The validated filters ready to be turned into a query.

    Raises:
        UnsupportedFilterError: If a parameter is not registered for the dataset.
        MissingRequiredFilterError: If a required filter is absent.
        InvalidFilterValueError: If a value is outside the controlled vocabulary.
        CatalogConfigurationError: If a derived filter references an unknown rule.
    """
    reserved = reserved_parameters or set()
    registered_names = set(dataset.filter_names)

    # Reject any parameter that is neither a control parameter nor a registered filter.
    for parameter_name in raw_filters:
        # Control parameters are handled by the endpoint, not by the dataset.
        if parameter_name in reserved:
            continue
        # Unknown parameters must fail fast with the list of valid filters.
        if parameter_name not in registered_names:
            raise UnsupportedFilterError(
                f"Parameter '{parameter_name}' is not supported by dataset '{dataset.id}'.",
                parameter=parameter_name,
                allowed_values=sorted(registered_names),
            )

    partition_values: dict[str, list[str]] = {}
    column_filters: dict[str, list[str]] = {}
    derived_conditions: list[DerivedCondition] = []
    applied: dict[str, str | list[str]] = {}
    filter_names_by_column: dict[str, str] = {}
    file_pattern_keys = [dataset.storage.default_file_pattern]

    # Resolve every registered filter: supplied values, default value or required check.
    for filter_definition in dataset.filters:
        raw_value = raw_filters.get(filter_definition.name)
        values = split_values(filter_definition, raw_value) if raw_value is not None else []

        # Fall back to the catalog default when the client sent nothing usable.
        if not values and filter_definition.default is not None:
            values = [filter_definition.default]

        # A required filter with neither a value nor a default stops the request.
        if not values:
            # Only required filters are mandatory; optional ones are simply skipped.
            if filter_definition.required:
                raise MissingRequiredFilterError(
                    f"Parameter '{filter_definition.name}' is required for dataset '{dataset.id}'.",
                    parameter=filter_definition.name,
                    allowed_values=filter_definition.allowed_values,
                )
            continue

        # Derived filters are validated by their own rule, one alternative per value.
        if filter_definition.source is FilterSource.DERIVED:
            try:
                rule = resolve_derived_rule(filter_definition.resolved_target)
            except KeyError as error:
                raise CatalogConfigurationError(
                    f"Filter '{filter_definition.name}' references unknown derived rule "
                    f"'{filter_definition.resolved_target}'."
                ) from error
            alternatives = [rule(filter_definition.name, value) for value in values]
            derived_conditions.append(DerivedCondition(filter_definition.name, alternatives))
            applied[filter_definition.name] = values[0] if len(values) == 1 else values
            continue

        canonical = [normalize_value(filter_definition, value) for value in values]
        applied[filter_definition.name] = canonical[0] if len(canonical) == 1 else canonical

        # Partition filters select folders (or file name fragments) in the storage path.
        if filter_definition.source is FilterSource.PARTITION:
            partition_values[filter_definition.resolved_target] = canonical
        # File pattern filters select which files are read inside the leaf folder.
        elif filter_definition.source is FilterSource.FILE_PATTERN:
            file_pattern_keys = canonical
        # Column filters become membership predicates inside the files.
        else:
            column_filters[filter_definition.resolved_target] = canonical
            filter_names_by_column[filter_definition.resolved_target] = filter_definition.name

    return ValidatedFilters(
        partition_values=partition_values,
        column_filters=column_filters,
        derived_conditions=derived_conditions,
        file_pattern_keys=file_pattern_keys,
        applied=applied,
        filter_names_by_column=filter_names_by_column,
    )
