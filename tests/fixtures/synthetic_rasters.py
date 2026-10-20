"""Synthetic GeoTIFF series mirroring the layouts of the raster datasets.

Observed monthly files: one single-band file per month with values equal to the month
number (January = 1, ... December = 12) over a 20 x 20 grid at 0.5 degrees covering
longitude 30..40 and latitude -5..5, with the north-west quarter set to nodata.

Scenario files: one twelve-band file per year (bands described as ``YYYY_MM``) with
values equal to ``year - 2040`` plus ``month / 100``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

GRID_SIZE = 20
RESOLUTION = 0.5
ORIGIN = (30.0, 5.0)  # top-left corner (lon, lat)
OBSERVED_PREFIX = (
    "domain=climate/type=observational/source=chirps-chirts-era5/region=africa/"
    "processing=monthly/variable={variable}"
)
SCENARIO_PREFIX = (
    "domain=climate/type=hazard-indices/source={source}/region={region}/model={model}/"
    "scenario={scenario}/time={time}/variable={variable}/processing=analysis-ready"
)


def _profile(count: int) -> dict:
    """Build the rasterio profile shared by every synthetic file."""
    return {
        "driver": "GTiff",
        "dtype": "float32",
        "count": count,
        "width": GRID_SIZE,
        "height": GRID_SIZE,
        "crs": "EPSG:4326",
        "transform": from_origin(ORIGIN[0], ORIGIN[1], RESOLUTION, RESOLUTION),
        "nodata": np.nan,
        "tiled": False,
    }


def _layer(value: float) -> np.ndarray:
    """Build one layer filled with ``value`` and nodata in the north-west quarter."""
    data = np.full((GRID_SIZE, GRID_SIZE), value, dtype=np.float32)
    data[: GRID_SIZE // 2, : GRID_SIZE // 2] = np.nan
    return data


def write_observed_series(root: Path, variable: str, months: list[tuple[int, int]]) -> None:
    """Write one single-band monthly file per ``(year, month)``."""
    folder = root / OBSERVED_PREFIX.format(variable=variable)
    folder.mkdir(parents=True, exist_ok=True)
    # Each month is a separate file named like the real bucket.
    for year, month in months:
        with rasterio.open(
            folder / f"{variable}-{year}-{month:02d}.tif", "w", **_profile(1)
        ) as target:
            target.write(_layer(float(month)), 1)


def write_scenario_year(
    root: Path,
    *,
    source: str,
    region: str,
    model: str,
    scenario: str,
    time: str,
    variable: str,
    year: int,
) -> None:
    """Write one twelve-band yearly file with monthly band descriptions."""
    folder = root / SCENARIO_PREFIX.format(
        source=source, region=region, model=model, scenario=scenario, time=time, variable=variable
    )
    folder.mkdir(parents=True, exist_ok=True)
    with rasterio.open(folder / f"{variable}_{scenario}_{year}.tif", "w", **_profile(12)) as target:
        # Every band holds a value that identifies its year and month.
        for month in range(1, 13):
            target.write(_layer(float(year - 2040) + month / 100), month)
            target.set_band_description(month, f"{year}_{month:02d}")


def build_raster_tree(root: Path) -> Path:
    """Create the synthetic raster files under ``root``.

    Args:
        root: Bucket root (shared with the tabular synthetic tree).

    Returns:
        The same root.
    """
    months_2020 = [(2020, month) for month in range(1, 13)]
    write_observed_series(root, "PTOT", months_2020 + [(2021, 1), (2021, 2)])
    write_observed_series(root, "SPEI-03", months_2020)
    # Two projected years of one scenario, plus one historic year.
    for year in (2041, 2042):
        write_scenario_year(
            root,
            source="nex-gddp-cmip6",
            region="global",
            model="MPI-ESM1-2-HR",
            scenario="ssp245",
            time="2041-2060",
            variable="ndd",
            year=year,
        )
    write_scenario_year(
        root,
        source="atlas_cmip6",
        region="ssa",
        model="historic",
        scenario="historic",
        time="1995-2014",
        variable="ndd",
        year=1995,
    )
    return root
