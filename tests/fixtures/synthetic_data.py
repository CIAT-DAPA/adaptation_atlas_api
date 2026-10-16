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


def _write_phenology(root: Path) -> None:
    """Write per-country phenology files (standard and plus-rain variants).

    Args:
        root: Storage root directory.
    """
    folder = (
        "domain=phenology/type=sos_eos/source=glass_nvdi/region=africa/processing=raw/level=adm1"
    )
    # One pair of files per country, mirroring ``KEN_seasonal-phenology[_plus-rain].parquet``.
    for iso3, regions in (("KEN", ["Nairobi", "Nakuru"]), ("ETH", ["Oromia"])):
        base: dict[str, list] = {"admin1_name": [], "year": [], "season": [], "sos": [], "eos": []}
        # Two seasons per region and year.
        for region in regions:
            for year in (2019, 2020):
                for season in ("1", "2"):
                    base["admin1_name"].append(region)
                    base["year"].append(year)
                    base["season"].append(season)
                    base["sos"].append(60 if season == "1" else 240)
                    base["eos"].append(150 if season == "1" else 330)
        _write(root, f"{folder}/{iso3}_seasonal-phenology.parquet", base)
        with_rain = dict(base)
        with_rain["rain_total"] = [float(100 + index) for index in range(len(base["year"]))]
        _write(root, f"{folder}/{iso3}_seasonal-phenology_plus-rain.parquet", with_rain)


def _write_climate_observed(root: Path) -> None:
    """Write single-file observed climate tables for admin0 and admin1.

    Args:
        root: Storage root directory.
    """
    folder = "domain=climate/type=observational/source=chirps-chirts-era5/region=africa"
    # Monthly and period aggregations live in sibling processing folders.
    for processing in ("admin-monthly", "admin-periods"):
        for level in ("0", "1"):
            columns: dict[str, list] = {
                "iso3": [],
                "admin0_name": [],
                "admin1_name": [],
                "year": [],
                "month": [],
                "variable": [],
                "value": [],
                "unit": [],
            }
            regions = [None] if level == "0" else ["Nairobi", "Nakuru"]
            # One row per region, month and variable for a single year.
            for region in regions:
                for month in (1, 2):
                    for variable, unit in (("prec", "mm"), ("tmax", "C")):
                        columns["iso3"].append("KEN")
                        columns["admin0_name"].append("Kenya")
                        columns["admin1_name"].append(region)
                        columns["year"].append(2020)
                        columns["month"].append(month)
                        columns["variable"].append(variable)
                        columns["value"].append(float(month * 10))
                        columns["unit"].append(unit)
            _write(
                root, f"{folder}/processing={processing}/variable=adm{level}_obs.parquet", columns
            )


def _write_hazard_change(root: Path) -> None:
    """Write two hazard-change indicator files with different schemas.

    Args:
        root: Storage root directory.
    """
    folder = (
        "domain=climate/type=hazard-indices/source=nex-gddp-cmip6/region=africa"
        "/processing=hazard-change/timeframe=annual"
    )
    ensemble: dict[str, list] = {
        "iso3": [],
        "admin0_name": [],
        "admin1_name": [],
        "admin2_name": [],
        "scenario": [],
        "hazard": [],
        "severity": [],
        "mean": [],
    }
    # Frequency ensemble rows for two countries, two hazards and two severities.
    for iso3, admin0 in (("KEN", "Kenya"), ("ETH", "Ethiopia")):
        for hazard in ("NDWS", "NDWL0"):
            for severity in ("severe", "extreme"):
                ensemble["iso3"].append(iso3)
                ensemble["admin0_name"].append(admin0)
                ensemble["admin1_name"].append(None)
                ensemble["admin2_name"].append(None)
                ensemble["scenario"].append("ssp245")
                ensemble["hazard"].append(hazard)
                ensemble["severity"].append(severity)
                ensemble["mean"].append(float("nan") if hazard == "NDWL0" else 1.5)
    _write(root, f"{folder}/variable=haz_freq_ensemble.parquet", ensemble)
    # Precipitation change rows have no hazard/severity/admin2 columns at all.
    change: dict[str, list] = {
        "iso3": ["KEN", "KEN"],
        "admin0_name": ["Kenya", "Kenya"],
        "admin1_name": [None, "Nakuru"],
        "scenario": ["ssp245", "ssp245"],
        "direction": ["increase_5", "decrease_5"],
        "mean": [40.0, 60.0],
    }
    _write(root, f"{folder}/variable=ptot_change_ensemble.parquet", change)


def _write_gyga(root: Path) -> None:
    """Write a GYGA-like crop file with a binary geometry column.

    Args:
        root: Storage root directory.
    """
    folder = "domain=impacts/type=production/source=gyga/region=ssa/processing=raw"
    table = pa.table(
        {
            "station_id": [1.0, 2.0],
            "crop": ["bean", "bean"],
            "yield_rfd": [1.2, 2.4],
            "admin0_name": ["Kenya", "Kenya"],
            "admin1_name": ["Nakuru", "Nairobi"],
            "admin2_name": ["Naivasha", "Westlands"],
            "geometry": pa.array([b"\x01\x02", b"\x03\x04"], type=pa.binary()),
            "geometry_bbox": pa.array(
                [{"xmin": 1.0, "ymin": 2.0}, {"xmin": 3.0, "ymin": 4.0}],
                type=pa.struct([("xmin", pa.float32()), ("ymin", pa.float32())]),
            ),
        }
    )
    path = root / f"{folder}/bean.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def build_storage_tree(root: Path) -> Path:
    """Create the synthetic bucket tree under ``root``.

    Args:
        root: Directory used as the local storage root.

    Returns:
        ``root`` for convenience.
    """
    _write_phenology(root)
    _write_climate_observed(root)
    _write_hazard_change(root)
    _write_gyga(root)
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
