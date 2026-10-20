"""Unit tests for window generation and prorated aggregation."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from adaptation_atlas_api.raster.domain import temporal_windows as tw
from adaptation_atlas_api.raster.domain.models import RasterLayer, TemporalWindow
from adaptation_atlas_api.shared.domain.dataset import AggregationMethod
from adaptation_atlas_api.shared.domain.exceptions import UnsupportedAggregationError


def monthly_layers(year: int, months: range) -> list[RasterLayer]:
    return [
        RasterLayer(f"file-{m}", 1, dt.date(year, m, 1), tw.month_end(year, m), f"{year}-{m:02d}")
        for m in months
    ]


def test_calendar_months_are_clipped_to_period() -> None:
    windows = tw.calendar_months(dt.date(2020, 1, 15), dt.date(2020, 3, 10))
    assert [w.label for w in windows] == ["2020-01", "2020-02", "2020-03"]
    assert windows[0].start == dt.date(2020, 1, 15)
    assert windows[-1].end == dt.date(2020, 3, 10)
    assert windows[1].days == 29  # leap year February


def test_fixed_windows_start_on_the_given_date() -> None:
    windows = tw.fixed_windows(dt.date(2020, 1, 10), dt.date(2020, 3, 5), 30)
    assert windows[0].start == dt.date(2020, 1, 10)
    assert windows[0].end == dt.date(2020, 2, 8)
    assert windows[1].start == dt.date(2020, 2, 9)
    assert windows[-1].end == dt.date(2020, 3, 5)
    with pytest.raises(UnsupportedAggregationError):
        tw.fixed_windows(dt.date(2020, 1, 1), dt.date(2020, 2, 1), 0)


def test_crop_season_from_duration_or_end() -> None:
    [season] = tw.crop_season(dt.date(2020, 3, 15), None, 120)
    assert season.end == dt.date(2020, 7, 12)
    [season] = tw.crop_season(dt.date(2020, 3, 15), dt.date(2020, 6, 30), None)
    assert season.days == 108
    with pytest.raises(UnsupportedAggregationError):
        tw.crop_season(dt.date(2020, 3, 15), None, None)
    with pytest.raises(UnsupportedAggregationError):
        tw.crop_season(dt.date(2020, 3, 15), dt.date(2020, 3, 1), None)


def test_explicit_windows_reject_duplicates_and_reversed() -> None:
    windows = tw.explicit_windows(
        [
            ("a", dt.date(2020, 1, 1), dt.date(2020, 1, 31)),
            ("b", dt.date(2020, 2, 1), dt.date(2020, 2, 10)),
        ]
    )
    assert [w.label for w in windows] == ["a", "b"]
    with pytest.raises(UnsupportedAggregationError):
        tw.explicit_windows([("a", dt.date(2020, 1, 1), dt.date(2020, 1, 2))] * 2)
    with pytest.raises(UnsupportedAggregationError):
        tw.explicit_windows([("a", dt.date(2020, 1, 5), dt.date(2020, 1, 2))])
    with pytest.raises(UnsupportedAggregationError):
        tw.explicit_windows([])


def test_overlap_days() -> None:
    assert (
        tw.overlap_days(
            dt.date(2020, 1, 1), dt.date(2020, 1, 31), dt.date(2020, 1, 20), dt.date(2020, 2, 20)
        )
        == 12
    )
    assert (
        tw.overlap_days(
            dt.date(2020, 1, 1), dt.date(2020, 1, 31), dt.date(2020, 2, 1), dt.date(2020, 2, 2)
        )
        == 0
    )


def test_prorated_sum_over_partial_months() -> None:
    layers = monthly_layers(2020, range(1, 4))
    # Each layer is a single cell whose value is the month number.
    data = np.array([[[1.0]], [[2.0]], [[3.0]]], dtype=np.float32)
    # 16 Jan .. 15 Feb: 16 of 31 January days + 15 of 29 February days.
    window = TemporalWindow("w", dt.date(2020, 1, 16), dt.date(2020, 2, 15))
    out, described = tw.aggregate_windows(data, layers, [window], AggregationMethod.SUM)
    expected = 1.0 * 16 / 31 + 2.0 * 15 / 29
    assert out.shape == (1, 1, 1)
    assert out[0, 0, 0] == pytest.approx(expected, rel=1e-5)
    assert described[0].coverage == 1.0
    assert described[0].source_layers == ["2020-01", "2020-02"]


def test_day_weighted_mean_and_extremes() -> None:
    layers = monthly_layers(2020, range(1, 3))
    data = np.array([[[10.0, np.nan]], [[20.0, 4.0]]], dtype=np.float32)
    window = TemporalWindow("w", dt.date(2020, 1, 1), dt.date(2020, 2, 29))
    mean, _ = tw.aggregate_windows(data, layers, [window], AggregationMethod.MEAN)
    assert mean[0, 0, 0] == pytest.approx((10 * 31 + 20 * 29) / 60)
    # A cell missing in one layer uses the other layer only.
    assert mean[0, 0, 1] == pytest.approx(4.0)
    maximum, _ = tw.aggregate_windows(data, layers, [window], AggregationMethod.MAX)
    minimum, _ = tw.aggregate_windows(data, layers, [window], AggregationMethod.MIN)
    assert maximum[0, 0, 0] == 20.0 and minimum[0, 0, 0] == 10.0
    assert minimum[0, 0, 1] == 4.0


def test_window_without_layers_is_nan_with_zero_coverage() -> None:
    layers = monthly_layers(2020, range(1, 2))
    data = np.ones((1, 2, 2), dtype=np.float32)
    window = TemporalWindow("w", dt.date(2021, 1, 1), dt.date(2021, 1, 31))
    out, described = tw.aggregate_windows(data, layers, [window], AggregationMethod.SUM)
    assert np.isnan(out).all()
    assert described[0].coverage == 0.0
    # Partial coverage is reported when the window extends beyond the data.
    half = TemporalWindow("h", dt.date(2020, 1, 1), dt.date(2020, 3, 1))
    _, described = tw.aggregate_windows(data, layers, [half], AggregationMethod.SUM)
    assert described[0].coverage == pytest.approx(31 / 61)
