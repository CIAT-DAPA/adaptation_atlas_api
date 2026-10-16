"""Unit tests for the filter validation rules (HU-001 exception flows)."""

from __future__ import annotations

import pytest

from adaptation_atlas_api.domain.exceptions import (
    InvalidFilterValueError,
    MissingRequiredFilterError,
    UnsupportedFilterError,
)
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.models.query import PredicateOperator
from adaptation_atlas_api.domain.validation.filters import validate_filters

CONTROL = {"format", "limit", "offset"}


def test_required_filters_are_enforced(hazard_dataset: DatasetDefinition) -> None:
    with pytest.raises(MissingRequiredFilterError) as error:
        validate_filters(hazard_dataset, {"country": "KEN"}, CONTROL)
    assert error.value.parameter == "period"
    assert "historic" in (error.value.allowed_values or [])


def test_unknown_parameter_is_rejected_with_valid_names(hazard_dataset: DatasetDefinition) -> None:
    with pytest.raises(UnsupportedFilterError) as error:
        validate_filters(
            hazard_dataset, {"country": "KEN", "period": "historic", "foo": "x"}, CONTROL
        )
    assert error.value.parameter == "foo"
    assert "crop" in (error.value.allowed_values or [])


def test_control_parameters_are_ignored(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(
        hazard_dataset,
        {"country": "KEN", "period": "historic", "format": "csv", "limit": "5"},
        CONTROL,
    )
    assert validated.applied["country"] == "KEN"


def test_invalid_vocabulary_value_lists_allowed_values(hazard_dataset: DatasetDefinition) -> None:
    with pytest.raises(InvalidFilterValueError) as error:
        validate_filters(
            hazard_dataset, {"country": "KEN", "period": "historic", "crop": "mango"}, CONTROL
        )
    assert error.value.parameter == "crop"
    assert "maize" in (error.value.allowed_values or [])


def test_country_is_case_insensitive_and_normalized(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(hazard_dataset, {"country": "ken", "period": "historic"}, CONTROL)
    assert validated.column_filters["iso3"] == ["KEN"]
    assert validated.applied["country"] == "KEN"


def test_filters_are_grouped_by_resolution_strategy(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(
        hazard_dataset,
        {
            "country": "KEN",
            "period": "historic",
            "severity": "severe",
            "season_type": "annual",
            "hazard_type": "solo",
            "crop": "maize",
            "admin_level": "1",
        },
        CONTROL,
    )
    assert validated.partition_values == {
        "source": ["atlas_cmip6"],
        "severity": ["severe"],
        "period": ["annual"],
    }
    assert validated.column_filters == {
        "iso3": ["KEN"],
        "timeframe": ["historic"],
        "crop": ["maize"],
    }
    assert validated.file_pattern_keys == ["solo"]
    assert len(validated.derived_conditions) == 1
    [alternative] = validated.derived_conditions[0].alternatives
    operators = [(p.column, p.operator) for p in alternative]
    assert operators == [
        ("admin1_name", PredicateOperator.IS_NOT_NULL),
        ("admin2_name", PredicateOperator.IS_NULL),
    ]


def test_default_values_are_applied(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(hazard_dataset, {"country": "KEN", "period": "historic"}, CONTROL)
    assert validated.partition_values["source"] == ["atlas_cmip6"]
    assert validated.file_pattern_keys == ["interaction"]


def test_invalid_admin_level_is_rejected(hazard_dataset: DatasetDefinition) -> None:
    with pytest.raises(InvalidFilterValueError) as error:
        validate_filters(
            hazard_dataset, {"country": "KEN", "period": "historic", "admin_level": "9"}, CONTROL
        )
    assert error.value.parameter == "admin_level"
    assert error.value.allowed_values == ["0", "1", "2"]


def test_empty_value_is_treated_as_absent(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(
        hazard_dataset, {"country": "KEN", "period": "historic", "crop": " "}, CONTROL
    )
    assert "crop" not in validated.column_filters


def test_multiple_values_from_list_and_comma(hazard_dataset: DatasetDefinition) -> None:
    validated = validate_filters(
        hazard_dataset,
        {
            "country": "KEN",
            "period": "historic",
            "crop": ["maize", "bean", "maize"],
            "severity": "severe, extreme",
            "admin_level": "0,1",
        },
        CONTROL,
    )
    assert validated.column_filters["crop"] == ["maize", "bean"]
    assert validated.partition_values["severity"] == ["severe", "extreme"]
    assert validated.applied["crop"] == ["maize", "bean"]
    assert len(validated.derived_conditions[0].alternatives) == 2


def test_invalid_value_inside_a_list_is_reported(hazard_dataset: DatasetDefinition) -> None:
    with pytest.raises(InvalidFilterValueError) as error:
        validate_filters(
            hazard_dataset,
            {"country": "KEN", "period": "historic", "crop": "maize,mango"},
            CONTROL,
        )
    assert error.value.parameter == "crop"
    assert "mango" in error.value.message


def test_single_valued_filter_rejects_lists() -> None:
    from adaptation_atlas_api.domain.models.dataset import FilterDefinition, FilterSource
    from adaptation_atlas_api.domain.validation.filters import split_values

    definition = FilterDefinition(name="x", source=FilterSource.COLUMN, multiple=False)
    with pytest.raises(InvalidFilterValueError):
        split_values(definition, ["a", "b"])
    assert split_values(definition, "a,b") == ["a,b"]
