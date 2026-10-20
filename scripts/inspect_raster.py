"""Inspect GeoTIFF files in S3 (or locally) to help register raster datasets in the catalog.

For every prefix or file given, the script reports CRS, resolution, extent, data type,
nodata, band count and descriptions, tiling/overviews (whether range reads are
efficient) and, for the first file of each folder, value statistics. The output is a
JSON document that can be shared with whoever writes the catalog YAML.

Usage::

    uv run python scripts/inspect_raster.py \
        "domain=climate/type=observational/.../processing=monthly/variable=PTOT/" \
        --output raster_inspection.json

    uv run python scripts/inspect_raster.py --local /data/bucket "domain=climate/"

Options:
    --output PATH    Write the JSON report to PATH (default: print to stdout).
    --local ROOT     Read files under ROOT instead of S3.
    --max-files N    Open at most N files per folder (default: 1); the rest are listed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

# Allow running the script from the repository root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaptation_atlas_api.shared.infrastructure.config import get_settings  # noqa: E402
from adaptation_atlas_api.shared.infrastructure.local_storage import LocalStorage  # noqa: E402
from adaptation_atlas_api.shared.infrastructure.s3_storage import S3Storage  # noqa: E402
from adaptation_atlas_api.shared.ports.storage import Storage  # noqa: E402

logger = logging.getLogger("inspect_raster")

RASTER_SUFFIXES = (".tif", ".tiff")


def build_storage(local_root: str | None) -> Storage:
    """Create the storage adapter used to list and read files.

    Args:
        local_root: Local directory to use instead of S3, or ``None``.

    Returns:
        A :class:`Storage` adapter.
    """
    # A local root takes precedence so the script can be used offline.
    if local_root:
        return LocalStorage(Path(local_root))
    settings = get_settings()
    return S3Storage(
        bucket=settings.s3_bucket,
        region=settings.aws_region,
        access_key_id=settings.aws_access_key_id,
        secret_access_key=settings.aws_secret_access_key,
        session_token=settings.aws_session_token,
        endpoint_url=settings.s3_endpoint_url,
    )


def describe_file(storage: Storage, key: str, *, with_stats: bool) -> dict[str, Any]:
    """Describe one GeoTIFF.

    Args:
        storage: Storage adapter used to resolve the URI.
        key: File key relative to the storage root.
        with_stats: Whether to read the first band and compute statistics.

    Returns:
        The file description, or an ``error`` entry when it cannot be opened.
    """
    uri = storage.resolve_uri(key)
    try:
        with rasterio.open(uri) as dataset:
            profile = dataset.profile
            report: dict[str, Any] = {
                "crs": dataset.crs.to_string() if dataset.crs else None,
                "width": dataset.width,
                "height": dataset.height,
                "resolution": [abs(dataset.res[0]), abs(dataset.res[1])],
                "bounds": list(dataset.bounds),
                "bands": dataset.count,
                "dtype": dataset.dtypes[0],
                "nodata": None if dataset.nodata is None else str(dataset.nodata),
                "band_descriptions": list(dataset.descriptions),
                "tiled": bool(profile.get("tiled")),
                "block_size": list(dataset.block_shapes[0]),
                "compression": profile.get("compress"),
                "overviews": dataset.overviews(1),
                "tags": dataset.tags(),
            }
            # Statistics are optional because they read the whole first band.
            if with_stats:
                data = dataset.read(1, masked=True)
                report["band_1_stats"] = {
                    "min": float(data.min()),
                    "mean": float(data.mean()),
                    "max": float(data.max()),
                    "nodata_percent": round(float(np.ma.getmaskarray(data).mean() * 100), 2),
                }
            return report
    except rasterio.errors.RasterioIOError as error:
        # A broken file must not stop the whole inspection.
        return {"error": str(error).splitlines()[0]}


def inspect_prefixes(
    prefixes: list[str], *, local_root: str | None, max_files: int
) -> dict[str, Any]:
    """Inspect every GeoTIFF under the given prefixes, grouped by folder.

    Args:
        prefixes: Storage prefixes (folders) or exact GeoTIFF keys.
        local_root: Local directory to use instead of S3, or ``None``.
        max_files: Files opened per folder; the others are only listed.

    Returns:
        Prefix -> folder -> report with file count, example names and descriptions.
    """
    storage = build_storage(local_root)
    results: dict[str, Any] = {}

    # Walk each prefix, grouping its GeoTIFFs by folder before opening them.
    for prefix in prefixes:
        # An exact key is inspected on its own; folders are listed.
        if prefix.endswith(RASTER_SUFFIXES):
            keys = [prefix]
        else:
            keys = [key for key in storage.list_files(prefix) if key.endswith(RASTER_SUFFIXES)]
        logger.info("Prefix %s: %d raster files", prefix, len(keys))
        folders: dict[str, list[str]] = defaultdict(list)
        for key in keys:
            folders[key.rpartition("/")[0]].append(key)
        results[prefix] = {}

        # Open only the first files of each folder; they usually share the layout.
        with rasterio.Env(**storage.gdal_options()):
            for folder, folder_keys in sorted(folders.items()):
                folder_keys.sort()
                logger.info("  %s (%d files)", folder, len(folder_keys))
                results[prefix][folder] = {
                    "files": len(folder_keys),
                    "first_file": folder_keys[0].rsplit("/", 1)[-1],
                    "last_file": folder_keys[-1].rsplit("/", 1)[-1],
                    "inspected": {
                        key.rsplit("/", 1)[-1]: describe_file(
                            storage, key, with_stats=(position == 0)
                        )
                        for position, key in enumerate(folder_keys[:max_files])
                    },
                }
    return results


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        argv: Arguments without the program name.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("prefixes", nargs="+", help="Storage prefixes or GeoTIFF keys.")
    parser.add_argument("--output", type=Path, default=None, help="JSON output path.")
    parser.add_argument("--local", default=None, metavar="ROOT", help="Local storage root.")
    parser.add_argument("--max-files", type=int, default=1, help="Files opened per folder.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    report = inspect_prefixes(
        arguments.prefixes, local_root=arguments.local, max_files=max(1, arguments.max_files)
    )
    text = json.dumps(report, indent=2, default=str)

    # Write to the requested file, or print when no output path was given.
    if arguments.output:
        arguments.output.write_text(text, encoding="utf-8")
        logger.info("Report written to %s", arguments.output)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
