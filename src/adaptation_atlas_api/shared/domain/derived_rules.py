"""Registry of derived filter rules.

A derived filter is a public parameter that does not map to a single column or
partition; instead a rule translates its value into one or more storage-agnostic
predicates. The first registered rule is ``admin_level``, which is encoded in the
Adaptation Atlas tables by the presence or absence of the administrative name
columns.
"""

from __future__ import annotations

from collections.abc import Callable

from adaptation_atlas_api.shared.domain.exceptions import InvalidFilterValueError
from adaptation_atlas_api.shared.domain.filters import Predicate, PredicateOperator

DerivedRule = Callable[[str, str], list[Predicate]]

ADMIN_LEVEL_VALUES = ["0", "1", "2"]


def admin_level_rule(parameter: str, value: str) -> list[Predicate]:
    """Translate an administrative level into null-pattern predicates.

    The Atlas tables encode the administrative level implicitly:

    * level 0 (country): ``admin1_name`` and ``admin2_name`` are ``NULL``.
    * level 1 (region): ``admin1_name`` is present and ``admin2_name`` is ``NULL``.
    * level 2 (district): ``admin2_name`` is present.

    Args:
        parameter: Public parameter name, used to build error messages.
        value: Requested level as a string (``"0"``, ``"1"`` or ``"2"``).

    Returns:
        Predicates that select only the rows of the requested level.

    Raises:
        InvalidFilterValueError: If ``value`` is not one of the supported levels.
    """
    normalized = value.strip()

    # Only the three administrative levels published by the Atlas are supported.
    if normalized not in ADMIN_LEVEL_VALUES:
        raise InvalidFilterValueError(
            f"Invalid value '{value}' for parameter '{parameter}'.",
            parameter=parameter,
            allowed_values=ADMIN_LEVEL_VALUES,
        )

    # Level 0 rows carry no sub-national names at all.
    if normalized == "0":
        return [
            Predicate("admin1_name", PredicateOperator.IS_NULL),
            Predicate("admin2_name", PredicateOperator.IS_NULL),
        ]

    # Level 1 rows have a region name but no district name.
    if normalized == "1":
        return [
            Predicate("admin1_name", PredicateOperator.IS_NOT_NULL),
            Predicate("admin2_name", PredicateOperator.IS_NULL),
        ]

    # Level 2 rows always carry a district name.
    return [Predicate("admin2_name", PredicateOperator.IS_NOT_NULL)]


DERIVED_RULES: dict[str, DerivedRule] = {
    "admin_level": admin_level_rule,
}


def resolve_derived_rule(rule_id: str) -> DerivedRule:
    """Return the rule registered under ``rule_id``.

    Args:
        rule_id: Identifier declared as the filter ``target`` in the catalog.

    Returns:
        The callable implementing the rule.

    Raises:
        KeyError: If no rule is registered under ``rule_id``.
    """
    return DERIVED_RULES[rule_id]
