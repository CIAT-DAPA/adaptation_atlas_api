"""Validation of the period and temporal aggregation of a raster request."""

from __future__ import annotations

import datetime as dt
from typing import Any

from adaptation_atlas_api.raster.domain import temporal_windows
from adaptation_atlas_api.raster.domain.models import AggregationType, TemporalAggregation
from adaptation_atlas_api.shared.domain.dataset import RasterDefinition
from adaptation_atlas_api.shared.domain.exceptions import (
    InvalidDateRangeError,
    UnsupportedAggregationError,
)


def parse_date(value: Any, parameter: str) -> dt.date | None:
    """Parse an ISO 8601 date (``YYYY-MM-DD``), accepting ``None`` and date objects.

    Args:
        value: Raw value.
        parameter: Parameter name for error messages.

    Returns:
        The date, or ``None`` when no value was given.

    Raises:
        InvalidDateRangeError: If the value is not a valid date.
    """
    # Absent values are allowed; the caller decides whether they are required.
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    # Datetimes are reduced to their date; dates pass through.
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip())
    except ValueError as error:
        raise InvalidDateRangeError(
            f"Parameter '{parameter}' must be a date formatted as YYYY-MM-DD.",
            parameter=parameter,
        ) from error


def validate_period(
    raster: RasterDefinition, start: Any, end: Any
) -> tuple[dt.date | None, dt.date | None]:
    """Validate the requested period.

    Args:
        raster: Raster description of the dataset.
        start: Raw start date.
        end: Raw end date.

    Returns:
        The ``(start, end)`` dates, ``None`` when omitted and allowed.

    Raises:
        InvalidDateRangeError: If a date is malformed, the start is after the end, or
            the dataset requires a period and one bound is missing.
    """
    start_date = parse_date(start, "start_date")
    end_date = parse_date(end, "end_date")
    # Datasets with long series require an explicit period.
    if raster.time_range_required and (start_date is None or end_date is None):
        raise InvalidDateRangeError(
            "Parameters 'start_date' and 'end_date' are required for this dataset.",
            parameter="start_date" if start_date is None else "end_date",
        )
    # Supplying only one bound is ambiguous.
    if (start_date is None) != (end_date is None):
        raise InvalidDateRangeError(
            "Parameters 'start_date' and 'end_date' must be supplied together.",
            parameter="start_date" if start_date is None else "end_date",
        )
    # The start must not be after the end.
    if start_date is not None and end_date is not None and start_date > end_date:
        raise InvalidDateRangeError(
            "Parameter 'start_date' is after 'end_date'.", parameter="start_date"
        )
    return start_date, end_date


def _required_date(payload: dict[str, Any], key: str) -> dt.date:
    """Read a mandatory date from the aggregation payload.

    Args:
        payload: Aggregation parameters sent by the client.
        key: Parameter name.

    Returns:
        The parsed date.

    Raises:
        UnsupportedAggregationError: If the parameter is missing.
    """
    value = parse_date(payload.get(key), f"aggregation.{key}")
    # Missing mandatory dates make the aggregation impossible to build.
    if value is None:
        raise UnsupportedAggregationError(
            f"Parameter 'aggregation.{key}' is required for this aggregation type.",
            parameter=f"aggregation.{key}",
        )
    return value


def build_aggregation(
    raster: RasterDefinition,
    payload: dict[str, Any] | None,
    start: dt.date | None,
    end: dt.date | None,
) -> TemporalAggregation:
    """Validate the aggregation request and generate its windows.

    Args:
        raster: Raster description of the dataset.
        payload: Aggregation parameters (``type`` plus type specific keys), or ``None``.
        start: Requested period start (bounds windows that derive from the period).
        end: Requested period end.

    Returns:
        The validated aggregation with its windows.

    Raises:
        UnsupportedAggregationError: If the type is unknown, not enabled for the dataset,
            or its parameters are missing or inconsistent.
    """
    parameters = dict(payload or {})
    raw_type = str(parameters.get("type", AggregationType.NONE.value)).strip().lower()
    # The type must be a known aggregation.
    if raw_type not in {item.value for item in AggregationType}:
        raise UnsupportedAggregationError(
            f"Aggregation type '{raw_type}' is unknown.",
            parameter="aggregation.type",
            allowed_values=raster.aggregations,
        )
    aggregation_type = AggregationType(raw_type)
    # The catalog may disable some aggregations for a dataset.
    if aggregation_type.value not in raster.aggregations:
        raise UnsupportedAggregationError(
            f"Aggregation '{aggregation_type.value}' is not supported by the dataset.",
            parameter="aggregation.type",
            allowed_values=raster.aggregations,
        )

    # No aggregation keeps the source time steps.
    if aggregation_type is AggregationType.NONE:
        return TemporalAggregation(aggregation_type)

    # Period based aggregations derive their windows from the requested period.
    if aggregation_type in (AggregationType.PERIOD, AggregationType.CALENDAR_MONTH):
        # These aggregations need the period bounds.
        if start is None or end is None:
            raise UnsupportedAggregationError(
                f"Aggregation '{aggregation_type.value}' requires 'start_date' and 'end_date'.",
                parameter="aggregation.type",
            )
        windows = (
            temporal_windows.whole_period(start, end)
            if aggregation_type is AggregationType.PERIOD
            else temporal_windows.calendar_months(start, end)
        )
        return TemporalAggregation(aggregation_type, windows, {"start": start, "end": end})

    # Fixed windows start on the given date (or the period start) and run to the end.
    if aggregation_type is AggregationType.FIXED_WINDOW:
        window_start = parse_date(parameters.get("start_date"), "aggregation.start_date") or start
        window_end = parse_date(parameters.get("end_date"), "aggregation.end_date") or end
        if window_start is None or window_end is None:
            raise UnsupportedAggregationError(
                "Aggregation 'fixed_window' requires a start and an end date.",
                parameter="aggregation.start_date",
            )
        try:
            window_days = int(parameters.get("window_days", 30))
        except (TypeError, ValueError) as error:
            raise UnsupportedAggregationError(
                "Parameter 'aggregation.window_days' must be an integer.",
                parameter="aggregation.window_days",
            ) from error
        windows = temporal_windows.fixed_windows(window_start, window_end, window_days)
        return TemporalAggregation(
            aggregation_type,
            windows,
            {"start": window_start, "end": window_end, "window_days": window_days},
        )

    # A crop season runs from planting to an explicit end or a duration.
    if aggregation_type is AggregationType.CROP_SEASON:
        planting = _required_date(parameters, "planting_date")
        season_end = parse_date(parameters.get("end_date"), "aggregation.end_date")
        duration = parameters.get("duration_days")
        try:
            duration_days = int(duration) if duration is not None else None
        except (TypeError, ValueError) as error:
            raise UnsupportedAggregationError(
                "Parameter 'aggregation.duration_days' must be an integer.",
                parameter="aggregation.duration_days",
            ) from error
        windows = temporal_windows.crop_season(planting, season_end, duration_days)
        return TemporalAggregation(
            aggregation_type,
            windows,
            {
                "planting_date": planting,
                "end_date": windows[0].end,
                "duration_days": duration_days,
            },
        )

    # Phenological stages are explicit labelled windows.
    raw_windows = parameters.get("windows")
    if not isinstance(raw_windows, list):
        raise UnsupportedAggregationError(
            "Aggregation 'stages' requires 'windows': a list of {label, start, end}.",
            parameter="aggregation.windows",
        )
    explicit: list[tuple[str, dt.date, dt.date]] = []
    # Every stage needs a label and both dates.
    for position, item in enumerate(raw_windows):
        if not isinstance(item, dict):
            raise UnsupportedAggregationError(
                "Every stage must be an object with 'label', 'start' and 'end'.",
                parameter="aggregation.windows",
            )
        label = str(item.get("label") or f"stage_{position + 1}")
        explicit.append((label, _required_date(item, "start"), _required_date(item, "end")))
    windows = temporal_windows.explicit_windows(explicit)
    return TemporalAggregation(aggregation_type, windows, {"windows": len(windows)})
