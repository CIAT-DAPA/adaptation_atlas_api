"""Builders of small synthetic Parquet files mirroring the Adaptation Atlas bucket layout.

The generated tree reproduces the real structure::

    domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure/
      variable=vop_intld15/period=annual/model=historic/severity=moderate/interaction.parquet
      variable=vop_intld15/period=annual/model=historic/severity=moderate/solo.parquet
    domain=hazard_exposure/source=nex-gddp-cmip6/.../variable=vop_nominal-usd21/period=jagermeyr/
      model=ENSEMBLEmean/severity=moderate/int=multi-hazard.parquet   (with GAUL codes)
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ATLAS_PREFIX = (
    "domain=hazard_exposure/source=atlas_cmip6/region=ssa/processing=hazard-risk-exposure"
)
NEX_PREFIX = (
    "domain=hazard_exposure/source=nex-gddp-cmip6/region=ssa/processing=hazard-risk-exposure"
)

# (iso3, admin0, admin1, admin2) rows covering the three administrative levels.
TERRITORIES = [
    ("KEN", "Kenya", None, None),
    ("KEN", "Kenya", "Nairobi", None),
    ("KEN", "Kenya", "Nairobi", "Westlands"),
    ("KEN", "Kenya", "Nakuru", None),
    ("KEN", "Kenya", "Nakuru", "Naivasha"),
    ("ETH", "Ethiopia", None, None),
    ("ETH", "Ethiopia", "Oromia", None),
    ("ETH", "Ethiopia", "Oromia", "Jimma"),
]

CROPS = ["maize", "bean", "cattle-tropical"]
HAZARDS = ["any", "dry", "heat", "dry+heat"]


def _build_rows(
    *,
    scenario_timeframes: list[tuple[str, str]],
    model: str,
    severity: str,
    hazard_vars: str,
    exposure_unit: str,
    with_gaul: bool,
) -> dict[str, list]:
    """Create the column arrays of one synthetic table.

    Args:
        scenario_timeframes: ``(scenario, timeframe)`` pairs to generate.
        model: Value of the ``model`` column.
        severity: Value of the ``severity`` column.
        hazard_vars: Value of the ``hazard_vars`` column.
        exposure_unit: Value of the ``exposure_unit`` column.
        with_gaul: Whether to include the GAUL code columns.

    Returns:
        Column name -> list of values.
    """
    columns: dict[str, list] = {
        "iso3": [],
        "admin0_name": [],
        "admin1_name": [],
        "admin2_name": [],
        "value": [],
        "scenario": [],
        "model": [],
        "timeframe": [],
        "hazard": [],
        "hazard_vars": [],
        "crop": [],
        "severity": [],
        "exposure_var": [],
        "exposure_unit": [],
    }
    # GAUL codes only exist in the newer source files.
    if with_gaul:
        columns.update({"gaul0_code": [], "gaul1_code": [], "gaul2_code": []})

    counter = 0
    # Generate one row per territory x scenario/timeframe x crop x hazard.
    for scenario, timeframe in scenario_timeframes:
        for index, (iso3, admin0, admin1, admin2) in enumerate(TERRITORIES):
            for crop in CROPS:
                for hazard in HAZARDS:
                    counter += 1
                    columns["iso3"].append(iso3)
                    columns["admin0_name"].append(admin0)
                    columns["admin1_name"].append(admin1)
                    columns["admin2_name"].append(admin2)
                    columns["value"].append(float(counter))
                    columns["scenario"].append(scenario)
                    columns["model"].append(model)
                    columns["timeframe"].append(timeframe)
                    columns["hazard"].append(hazard)
                    columns["hazard_vars"].append(hazard_vars)
                    columns["crop"].append(crop)
                    columns["severity"].append(severity)
                    columns["exposure_var"].append("vop")
                    columns["exposure_unit"].append(exposure_unit)
                    # GAUL codes are stored as doubles in the real files.
                    if with_gaul:
                        columns["gaul0_code"].append(100.0 + index)
                        columns["gaul1_code"].append(1000.0 + index if admin1 else None)
                        columns["gaul2_code"].append(10000.0 + index if admin2 else None)
    return columns


def _write(root: Path, relative_path: str, columns: dict[str, list]) -> Path:
    """Write ``columns`` as a Parquet file under ``root``.

    Args:
        root: Storage root directory.
        relative_path: Path relative to ``root``.
        columns: Column name -> values.

    Returns:
        The written file path.
    """
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)
    return path


def build_storage_tree(root: Path) -> Path:
    """Create the synthetic bucket tree under ``root``.

    Args:
        root: Directory used as the local storage root.

    Returns:
        ``root`` for convenience.
    """
    historic = [("historic", "historic")]
    atlas_leaf = (
        f"{ATLAS_PREFIX}/variable=vop_intld15/period=annual/model=historic/severity=moderate"
    )

    _write(
        root,
        f"{atlas_leaf}/interaction.parquet",
        _build_rows(
            scenario_timeframes=historic,
            model="historic",
            severity="moderate",
            hazard_vars="NDWS+NTx35+NDWL0",
            exposure_unit="intld15",
            with_gaul=False,
        ),
    )
    _write(
        root,
        f"{atlas_leaf}/solo.parquet",
        _build_rows(
            scenario_timeframes=historic,
            model="historic",
            severity="moderate",
            hazard_vars="NDWS+NTx35+NDWL0",
            exposure_unit="intld15",
            with_gaul=False,
        ),
    )
    # A second severity folder for the same model, to exercise partition filtering.
    _write(
        root,
        f"{ATLAS_PREFIX}/variable=vop_intld15/period=annual/model=historic/severity=severe/interaction.parquet",
        _build_rows(
            scenario_timeframes=historic,
            model="historic",
            severity="severe",
            hazard_vars="NDWS+NTx35+NDWL0",
            exposure_unit="intld15",
            with_gaul=False,
        ),
    )

    nex_leaf = (
        f"{NEX_PREFIX}/variable=vop_nominal-usd21/period=jagermeyr"
        "/model=ENSEMBLEmean/severity=moderate"
    )
    _write(
        root,
        f"{nex_leaf}/int=multi-hazard.parquet",
        _build_rows(
            scenario_timeframes=[("historic", "1995-2014"), ("ssp245", "2041-2060")],
            model="ENSEMBLEmean",
            severity="moderate",
            hazard_vars="NDWS+NTxM+NDWL0",
            exposure_unit="nominal-usd-2021",
            with_gaul=True,
        ),
    )
    sidecar = {
        "unit": "nominal-usd-2021",
        "field_descriptions": {"value": "Hazard frequency (0-1) x exposure value."},
    }
    (root / f"{nex_leaf}/int=multi-hazard.parquet.json").write_text(json.dumps(sidecar))
    return root
