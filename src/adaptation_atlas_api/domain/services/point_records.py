"""Conversion of point samples into tabular records.

Point results reuse the tabular machinery: the records are served as JSON by the
records response model and as CSV by the existing CSV exporter.
"""

from __future__ import annotations

import math
from typing import Any

from adaptation_atlas_api.domain.models.results import TabularResult
from adaptation_atlas_api.domain.models.spatial import RasterResult

POINT_COLUMNS = [
    "point",
    "longitude",
    "latitude",
    "period",
    "start",
    "end",
    "coverage",
    "variable",
    "value",
    "unit",
]


def point_samples_to_records(result: RasterResult) -> TabularResult:
    """Flatten a point result into one record per point and output slice.

    Args:
        result: A point result (``result.grid`` is ``None``).

    Returns:
        The records ordered by point then by slice, with ``NaN`` rendered as ``null``.
    """
    records: list[dict[str, Any]] = []

    # One record per (point, slice) pair keeps the table easy to pivot.
    for point_index, (lon, lat) in enumerate(result.points):
        for slice_index, layer in enumerate(result.output_layers):
            value = float(result.data[slice_index, point_index])
            records.append(
                {
                    "point": point_index,
                    "longitude": lon,
                    "latitude": lat,
                    "period": layer.label,
                    "start": layer.start.isoformat(),
                    "end": layer.end.isoformat(),
                    "coverage": round(layer.coverage, 4),
                    "variable": result.variable,
                    # NaN is not valid JSON; missing values become null.
                    "value": None if math.isnan(value) else value,
                    "unit": result.unit,
                }
            )
    applied: dict[str, str | list[str]] = dict(result.applied_filters)
    return TabularResult(
        dataset_id=result.dataset_id,
        columns=list(POINT_COLUMNS),
        records=records,
        total_count=len(records),
        limit=max(len(records), 1),
        offset=0,
        applied_filters=applied,
        source_files=result.source_files,
    )
