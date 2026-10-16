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
from adaptation_atlas_api.domain.models.query import Predicate, ValidatedFilters
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


def validate_filters(
    dataset: DatasetDefinition,
    raw_filters: Mapping[str, str | None],
    reserved_parameters: set[str] | None = None,
) -> ValidatedFilters:
    """Validate the filters supplied for ``dataset`` and group them by resolution strategy.

    Args:
        dataset: Definition the filters are validated against.
        raw_filters: Every query parameter received, including unknown ones, mapped to its
            raw value (``None`` when the parameter was sent without a value).
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

    partition_values: dict[str, str] = {}
    column_filters: dict[str, str] = {}
    derived_predicates: list[Predicate] = []
    applied: dict[str, str] = {}
    file_pattern_key = dataset.storage.default_file_pattern

    # Resolve every registered filter: supplied value, default value or required check.
    for filter_definition in dataset.filters:
        raw_value = raw_filters.get(filter_definition.name)

        # Treat a parameter sent without a value exactly like an absent parameter.
        if raw_value is not None and not raw_value.strip():
            raw_value = None

        # Fall back to the catalog default when the client did not send the filter.
        if raw_value is None:
            raw_value = filter_definition.default

        # A required filter with neither a value nor a default stops the request.
        if raw_value is None:
            # Only required filters are mandatory; optional ones are simply skipped.
            if filter_definition.required:
                raise MissingRequiredFilterError(
                    f"Parameter '{filter_definition.name}' is required for dataset '{dataset.id}'.",
                    parameter=filter_definition.name,
                    allowed_values=filter_definition.allowed_values,
                )
            continue

        # Derived filters are validated by their own rule, which produces predicates.
        if filter_definition.source is FilterSource.DERIVED:
            try:
                rule = resolve_derived_rule(filter_definition.resolved_target)
            except KeyError as error:
                raise CatalogConfigurationError(
                    f"Filter '{filter_definition.name}' references unknown derived rule "
                    f"'{filter_definition.resolved_target}'."
                ) from error
            derived_predicates.extend(rule(filter_definition.name, raw_value))
            applied[filter_definition.name] = raw_value.strip()
            continue

        canonical = normalize_value(filter_definition, raw_value)
        applied[filter_definition.name] = canonical

        # Partition filters select a folder in the storage path.
        if filter_definition.source is FilterSource.PARTITION:
            partition_values[filter_definition.resolved_target] = canonical
        # File pattern filters select which files are read inside the leaf folder.
        elif filter_definition.source is FilterSource.FILE_PATTERN:
            file_pattern_key = canonical
        # Column filters become equality predicates inside the files.
        else:
            column_filters[filter_definition.resolved_target] = canonical

    return ValidatedFilters(
        partition_values=partition_values,
        column_filters=column_filters,
        derived_predicates=derived_predicates,
        file_pattern_key=file_pattern_key,
        applied=applied,
    )
