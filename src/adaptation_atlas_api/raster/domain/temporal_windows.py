"""Temporal windows and aggregation of raster layers.

Windows are generated from the client's request (calendar months, fixed length
windows, crop seasons, phenological stages or the whole period) and then every window is
aggregated from the source layers that overlap it. When a window does not align with
the layer boundaries (e.g. a 30-day window over monthly data) each layer contributes
in proportion to the number of its days that fall inside the window.
"""

from __future__ import annotations

import calendar
import datetime as dt
import warnings
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from adaptation_atlas_api.raster.domain.models import OutputLayer, RasterLayer, TemporalWindow
from adaptation_atlas_api.shared.domain.dataset import AggregationMethod
from adaptation_atlas_api.shared.domain.exceptions import UnsupportedAggregationError

# Aggregations that can be prorated; min and max take every overlapping layer as is.
_PRORATED = (AggregationMethod.SUM, AggregationMethod.MEAN)


def month_end(year: int, month: int) -> dt.date:
    """Return the last day of a month.

    Args:
        year: Calendar year.
        month: Calendar month (1-12).

    Returns:
        The last date of the month.
    """
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def whole_period(start: dt.date, end: dt.date) -> list[TemporalWindow]:
    """Build a single window spanning the whole period.

    Args:
        start: First day.
        end: Last day.

    Returns:
        One window labelled ``start/end``.
    """
    return [TemporalWindow(f"{start.isoformat()}/{end.isoformat()}", start, end)]


def calendar_months(start: dt.date, end: dt.date) -> list[TemporalWindow]:
    """Build one window per calendar month touched by the period, clipped to it.

    Args:
        start: First day.
        end: Last day.

    Returns:
        Windows labelled ``YYYY-MM`` in chronological order.
    """
    windows: list[TemporalWindow] = []
    year, month = start.year, start.month

    # Walk month by month until the month of the end date is passed.
    while (year, month) <= (end.year, end.month):
        first = max(dt.date(year, month, 1), start)
        last = min(month_end(year, month), end)
        windows.append(TemporalWindow(f"{year:04d}-{month:02d}", first, last))
        # Advance to the next month, rolling the year over in December.
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return windows


def fixed_windows(start: dt.date, end: dt.date, window_days: int) -> list[TemporalWindow]:
    """Build consecutive windows of ``window_days`` days starting on ``start``.

    The last window is truncated at ``end``.

    Args:
        start: First day of the first window.
        end: Last day of the period.
        window_days: Length of every window in days.

    Returns:
        Windows labelled ``start/end`` in chronological order.

    Raises:
        UnsupportedAggregationError: If ``window_days`` is not positive.
    """
    # A window must span at least one day.
    if window_days < 1:
        raise UnsupportedAggregationError(
            "Parameter 'window_days' must be a positive number of days.",
            parameter="aggregation.window_days",
        )
    windows: list[TemporalWindow] = []
    cursor = start

    # Cut windows one after the other until the period is exhausted.
    while cursor <= end:
        last = min(cursor + dt.timedelta(days=window_days - 1), end)
        windows.append(TemporalWindow(f"{cursor.isoformat()}/{last.isoformat()}", cursor, last))
        cursor = last + dt.timedelta(days=1)
    return windows


def crop_season(
    planting_date: dt.date, end_date: dt.date | None, duration_days: int | None
) -> list[TemporalWindow]:
    """Build a single crop season window from the planting date.

    Args:
        planting_date: Crop establishment date.
        end_date: Explicit end of the season, or ``None`` to derive it from the duration.
        duration_days: Season length used when ``end_date`` is absent.

    Returns:
        One window labelled ``crop_season``.

    Raises:
        UnsupportedAggregationError: If neither an end date nor a duration is given, or
            the season ends before it starts.
    """
    # Without an explicit end the season length defines it.
    if end_date is None:
        # One of the two must be supplied to know when the season ends.
        if duration_days is None or duration_days < 1:
            raise UnsupportedAggregationError(
                "A crop season needs 'end_date' or a positive 'duration_days'.",
                parameter="aggregation.end_date",
            )
        end_date = planting_date + dt.timedelta(days=duration_days - 1)
    # A season cannot end before it starts.
    if end_date < planting_date:
        raise UnsupportedAggregationError(
            "The crop season end date is before the planting date.",
            parameter="aggregation.end_date",
        )
    return [TemporalWindow("crop_season", planting_date, end_date)]


def explicit_windows(windows: list[tuple[str, dt.date, dt.date]]) -> list[TemporalWindow]:
    """Build windows from explicit ``(label, start, end)`` definitions (e.g. stages).

    Args:
        windows: Label and bounds of every window.

    Returns:
        The windows in the order given.

    Raises:
        UnsupportedAggregationError: If no window is given, a label repeats, or a window
            ends before it starts.
    """
    # At least one window is needed to aggregate anything.
    if not windows:
        raise UnsupportedAggregationError(
            "At least one window is required.", parameter="aggregation.windows"
        )
    result: list[TemporalWindow] = []
    seen: set[str] = set()

    # Validate every window individually.
    for label, start, end in windows:
        # Labels identify output slices, so they must be unique.
        if label in seen:
            raise UnsupportedAggregationError(
                f"Window label '{label}' is repeated.", parameter="aggregation.windows"
            )
        # A window cannot end before it starts.
        if end < start:
            raise UnsupportedAggregationError(
                f"Window '{label}' ends before it starts.", parameter="aggregation.windows"
            )
        seen.add(label)
        result.append(TemporalWindow(label, start, end))
    return result


def overlap_days(
    first_start: dt.date, first_end: dt.date, second_start: dt.date, second_end: dt.date
) -> int:
    """Count the days shared by two closed date intervals.

    Args:
        first_start: Start of the first interval.
        first_end: End of the first interval.
        second_start: Start of the second interval.
        second_end: End of the second interval.

    Returns:
        Number of shared days, zero when the intervals are disjoint.
    """
    latest_start = max(first_start, second_start)
    earliest_end = min(first_end, second_end)
    return max(0, (earliest_end - latest_start).days + 1)


@dataclass(frozen=True)
class LayerWeight:
    """Contribution of one source layer to a window.

    Attributes:
        index: Position of the layer in the stack.
        fraction: Fraction of the layer's days inside the window (0 to 1).
        days: Number of layer days inside the window.
    """

    index: int
    fraction: float
    days: int


def layer_weights(window: TemporalWindow, layers: list[RasterLayer]) -> list[LayerWeight]:
    """Compute how much of every layer falls inside ``window``.

    Args:
        window: Target window.
        layers: Source layers in stack order.

    Returns:
        One weight per overlapping layer, in stack order.
    """
    weights: list[LayerWeight] = []

    # Keep only the layers that share at least one day with the window.
    for index, layer in enumerate(layers):
        shared = overlap_days(window.start, window.end, layer.start, layer.end)
        # Disjoint layers contribute nothing.
        if shared == 0:
            continue
        weights.append(LayerWeight(index, shared / layer.days, shared))
    return weights


def aggregate_windows(
    data: np.ndarray,
    layers: list[RasterLayer],
    windows: list[TemporalWindow],
    method: AggregationMethod,
) -> tuple[np.ndarray, list[OutputLayer]]:
    """Aggregate a layer stack over every window with the variable's rule.

    ``sum`` prorates each layer by the fraction of its days inside the window;
    ``mean`` weights each layer by its overlapping days; ``min`` and ``max`` take the
    extreme over every overlapping layer. Windows without any overlapping layer are
    filled with ``NaN`` and reported with zero coverage.

    Args:
        data: Array whose first axis indexes ``layers``.
        layers: Source layers describing the first axis of ``data``.
        windows: Windows to aggregate over.
        method: Aggregation rule of the variable.

    Returns:
        The aggregated array (first axis indexes ``windows``) and the description of
        every output slice, including the fraction of the window actually covered.
    """
    output = np.full((len(windows), *data.shape[1:]), np.nan, dtype=np.float32)
    described: list[OutputLayer] = []

    # Aggregate every window independently.
    for position, window in enumerate(windows):
        weights = layer_weights(window, layers)
        covered_days = sum(weight.days for weight in weights)
        coverage = covered_days / window.days
        sources = [layers[weight.index].label for weight in weights]
        described.append(OutputLayer(window.label, window.start, window.end, coverage, sources))
        # Windows with no data stay NaN.
        if not weights:
            continue
        indexes = [weight.index for weight in weights]
        selected = data[indexes]
        # Sums and means use prorated weights; extremes use the overlapping layers as is.
        if method in _PRORATED:
            output[position] = _weighted(selected, weights, method)
        elif method is AggregationMethod.MIN:
            output[position] = _nan_extreme(selected, np.nanmin)
        else:
            output[position] = _nan_extreme(selected, np.nanmax)
    return output, described


def _weighted(
    selected: np.ndarray, weights: list[LayerWeight], method: AggregationMethod
) -> np.ndarray:
    """Compute a prorated sum or a day-weighted mean over the selected layers.

    Cells that are ``NaN`` in every layer stay ``NaN``; cells with partial data use
    the layers that have a value.

    Args:
        selected: Array of the overlapping layers.
        weights: Weight of every selected layer, in the same order.
        method: ``sum`` or ``mean``.

    Returns:
        The aggregated slice.
    """
    valid = ~np.isnan(selected)
    filled = np.where(valid, selected, 0.0)
    shape = (len(weights),) + (1,) * (selected.ndim - 1)
    any_valid = valid.any(axis=0)

    # A prorated sum scales every layer by the fraction of its days in the window.
    if method is AggregationMethod.SUM:
        fractions = np.array([weight.fraction for weight in weights], dtype=np.float32)
        total = (filled * fractions.reshape(shape)).sum(axis=0)
        return np.where(any_valid, total, np.nan)

    # A mean weights every layer by its overlapping days, ignoring missing cells.
    days = np.array([weight.days for weight in weights], dtype=np.float32).reshape(shape)
    weighted_sum = (filled * days).sum(axis=0)
    weight_sum = (valid * days).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = weighted_sum / weight_sum
    return np.where(any_valid, mean, np.nan)


def _nan_extreme(selected: np.ndarray, reducer: Callable[..., np.ndarray]) -> np.ndarray:
    """Apply ``nanmin``/``nanmax`` without warnings on all-NaN cells.

    Args:
        selected: Array of the overlapping layers.
        reducer: ``np.nanmin`` or ``np.nanmax``.

    Returns:
        The reduced slice with ``NaN`` where no layer has a value.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.asarray(reducer(selected, axis=0))
