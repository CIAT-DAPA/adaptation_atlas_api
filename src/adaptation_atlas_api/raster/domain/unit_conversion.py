"""Unit conversion of raster values using the rules registered in the catalog.

Conversions are never inferred: a target unit is accepted only when the variable
declares a conversion to it. The source unit itself is always accepted (no-op).
"""

from __future__ import annotations

import numpy as np

from adaptation_atlas_api.raster.domain.models import ProcessingStep
from adaptation_atlas_api.shared.domain.dataset import VariableDefinition
from adaptation_atlas_api.shared.domain.exceptions import UnsupportedUnitError


def resolve_target_unit(
    variable: str, definition: VariableDefinition, requested: str | None
) -> str:
    """Validate the unit requested for a variable.

    Args:
        variable: Variable value, used in error messages.
        definition: Variable description with its conversions.
        requested: Unit asked by the client, or ``None`` to keep the source unit.

    Returns:
        The unit the output will use.

    Raises:
        UnsupportedUnitError: If the unit is neither the source unit nor a registered
            conversion target.
    """
    # Without a request the published unit is preserved.
    if requested is None or not requested.strip():
        return definition.unit
    unit = requested.strip()
    # Only the source unit and declared conversion targets are accepted.
    if unit not in definition.available_units:
        raise UnsupportedUnitError(
            f"Unit '{unit}' is not supported for variable '{variable}'; no conversion rule "
            "is registered.",
            parameter="unit",
            allowed_values=definition.available_units,
        )
    return unit


def convert_values(
    data: np.ndarray, definition: VariableDefinition, target_unit: str
) -> tuple[np.ndarray, ProcessingStep | None]:
    """Convert ``data`` from the source unit to ``target_unit``.

    Args:
        data: Values in the source unit (``NaN`` for nodata).
        definition: Variable description with its conversions.
        target_unit: Unit to convert to (already validated).

    Returns:
        The converted array and the processing step describing the conversion, or the
        original array and ``None`` when no conversion is needed.
    """
    # The source unit needs no conversion.
    if target_unit == definition.unit:
        return data, None
    rule = definition.conversions[target_unit]
    converted = (data.astype(np.float32) * np.float32(rule.factor)) + np.float32(rule.offset)
    step = ProcessingStep(
        step="unit_conversion",
        description=rule.description
        or f"Converted from {definition.unit} to {target_unit} (value * factor + offset).",
        parameters={
            "source_unit": definition.unit,
            "target_unit": target_unit,
            "factor": rule.factor,
            "offset": rule.offset,
        },
    )
    return converted, step
