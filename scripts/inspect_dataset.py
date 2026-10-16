"""Inspect Parquet datasets in S3 (or locally) to help register them in the catalog.

For every prefix or file given, the script reports the Parquet schema, the number of
rows, the number of files and the distinct values of the low-cardinality columns. The
output is a JSON document that can be shared with whoever writes the catalog YAML.

Credentials and bucket are read from ``.env`` through the application settings, so the
script works wherever the API works.

Usage::

    uv run python scripts/inspect_dataset.py \
        "domain=climate/type=observational/source=chirps-chirts-era5/region=africa/" \
        "domain=impacts/type=pest-disease/" \
        --output inspection.json

    # Inspect local files instead of S3 (STORAGE_BACKEND=local in .env or --local)
    uv run python scripts/inspect_dataset.py --local /data/bucket "domain=phenology/"

Options:
    --output PATH        Write the JSON report to PATH (default: print to stdout).
    --max-distinct N     Columns with more distinct values than N are summarised with
                         min/max only (default: 60).
    --sample-rows N      Rows of each file group included as an example (default: 3).
    --local ROOT         Read files under ROOT instead of S3.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb

# Allow running the script from the repository root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adaptation_atlas_api.adapters.storage.local_storage import LocalStorage  # noqa: E402
from adaptation_atlas_api.adapters.storage.s3_storage import S3Storage  # noqa: E402
from adaptation_atlas_api.infrastructure.config import get_settings  # noqa: E402
from adaptation_atlas_api.ports.storage import Storage  # noqa: E402

logger = logging.getLogger("inspect_dataset")

PARQUET_SUFFIX = ".parquet"
PARTITION_SEPARATOR = "="


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


def group_files(keys: list[str]) -> dict[str, list[str]]:
    """Group Parquet keys by their folder and generalized file name.

    Files that only differ by a partition value or a country code are grouped so that
    one schema inspection covers the whole group (e.g. ``KEN_x.parquet`` and
    ``ETH_x.parquet``).

    Args:
        keys: Parquet keys relative to the storage root.

    Returns:
        Group label -> list of keys in the group.
    """
    groups: dict[str, list[str]] = defaultdict(list)

    # Build a group label replacing the country code prefix and partition values.
    for key in keys:
        folder, _, name = key.rpartition("/")
        label_name = name
        # A three-letter upper-case prefix followed by ``_`` is treated as a country code.
        if len(name) > 4 and name[:3].isupper() and name[:3].isalpha() and name[3] == "_":
            label_name = "<ISO3>_" + name[4:]
        groups[f"{folder}/{label_name}"].append(key)
    return dict(groups)


def parse_partitions(key: str) -> dict[str, str]:
    """Extract ``name=value`` folder segments from a key.

    Args:
        key: Key relative to the storage root.

    Returns:
        Partition name -> value found in the folder path.
    """
    partitions: dict[str, str] = {}

    # Only folder segments are considered; the file name is skipped.
    for segment in key.split("/")[:-1]:
        name, separator, value = segment.partition(PARTITION_SEPARATOR)
        # Segments without ``=`` are plain folders, not partitions.
        if separator:
            partitions[name] = value
    return partitions


def quote(value: str) -> str:
    """Quote a SQL identifier.

    Args:
        value: Raw identifier.

    Returns:
        The identifier wrapped in double quotes.
    """
    return '"' + value.replace('"', '""') + '"'


def inspect_group(
    connection: duckdb.DuckDBPyConnection,
    storage: Storage,
    keys: list[str],
    *,
    max_distinct: int,
    sample_rows: int,
) -> dict[str, Any]:
    """Describe one group of files sharing the same schema.

    Args:
        connection: DuckDB connection with storage access configured.
        storage: Storage adapter used to resolve URIs.
        keys: Keys of the files in the group.
        max_distinct: Columns above this cardinality are summarised with min/max only.
        sample_rows: Number of example rows to include.

    Returns:
        A dictionary with schema, counts, distinct values and sample rows.
    """
    uris = [storage.resolve_uri(key) for key in keys]
    source = "read_parquet($uris, union_by_name=true)"
    report: dict[str, Any] = {
        "files": len(keys),
        "example_keys": keys[:3],
        "partitions_in_path": parse_partitions(keys[0]),
    }

    try:
        schema_rows = connection.execute(
            f"DESCRIBE SELECT * FROM {source}", {"uris": uris}
        ).fetchall()
        report["schema"] = [{"column": row[0], "type": row[1]} for row in schema_rows]
        report["row_count"] = connection.execute(
            f"SELECT COUNT(*) FROM {source}", {"uris": uris}
        ).fetchone()[0]
    except duckdb.Error as error:
        # A broken group must not stop the whole inspection.
        report["error"] = str(error).splitlines()[0]
        return report

    columns: dict[str, Any] = {}
    # Profile each column: cardinality, distinct values or range, null count.
    for row in schema_rows:
        column, column_type = row[0], row[1]
        identifier = quote(column)
        stats = connection.execute(
            f"SELECT COUNT(DISTINCT {identifier}), COUNT(*) FILTER (WHERE {identifier} IS NULL) "
            f"FROM {source}",
            {"uris": uris},
        ).fetchone()
        distinct_count, null_count = int(stats[0]), int(stats[1])
        profile: dict[str, Any] = {
            "type": column_type,
            "distinct": distinct_count,
            "nulls": null_count,
        }
        # Low-cardinality columns are candidates for controlled vocabularies.
        if distinct_count <= max_distinct:
            values = connection.execute(
                f"SELECT DISTINCT {identifier} FROM {source} WHERE {identifier} IS NOT NULL "
                f"ORDER BY 1",
                {"uris": uris},
            ).fetchall()
            profile["values"] = [value[0] for value in values]
        else:
            extremes = connection.execute(
                f"SELECT MIN({identifier}), MAX({identifier}) FROM {source}", {"uris": uris}
            ).fetchone()
            profile["min"] = extremes[0]
            profile["max"] = extremes[1]
        columns[column] = profile
    report["columns"] = columns

    sample = connection.execute(
        f"SELECT * FROM {source} LIMIT {int(sample_rows)}", {"uris": uris}
    ).fetchall()
    report["sample_rows"] = [
        dict(zip([row[0] for row in schema_rows], values, strict=True)) for values in sample
    ]
    return report


def inspect_prefixes(
    prefixes: list[str],
    *,
    local_root: str | None,
    max_distinct: int,
    sample_rows: int,
) -> dict[str, Any]:
    """Inspect every Parquet file found under the given prefixes.

    Args:
        prefixes: Storage prefixes (folders) or exact Parquet keys.
        local_root: Local directory to use instead of S3, or ``None``.
        max_distinct: Columns above this cardinality are summarised with min/max only.
        sample_rows: Number of example rows to include per group.

    Returns:
        Prefix -> group label -> inspection report.
    """
    storage = build_storage(local_root)
    connection = duckdb.connect(database=":memory:")
    storage.configure_connection(connection)
    results: dict[str, Any] = {}

    # Walk each prefix, grouping its Parquet files before inspecting them.
    for prefix in prefixes:
        # An exact Parquet key is inspected on its own; folders are listed.
        if prefix.endswith(PARQUET_SUFFIX):
            keys = [prefix]
        else:
            keys = [
                key
                for key in storage.list_files(prefix, suffix=PARQUET_SUFFIX)
                # Backup copies with a double extension are not datasets.
                if key.endswith(PARQUET_SUFFIX)
            ]
        logger.info("Prefix %s: %d parquet files", prefix, len(keys))
        groups = group_files(keys)
        results[prefix] = {}

        # Inspect each schema group once.
        for label, group_keys in sorted(groups.items()):
            logger.info("  inspecting %s (%d files)", label, len(group_keys))
            results[prefix][label] = inspect_group(
                connection,
                storage,
                group_keys,
                max_distinct=max_distinct,
                sample_rows=sample_rows,
            )
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
    parser.add_argument("prefixes", nargs="+", help="Storage prefixes or Parquet keys to inspect.")
    parser.add_argument("--output", type=Path, default=None, help="JSON output path.")
    parser.add_argument("--max-distinct", type=int, default=60)
    parser.add_argument("--sample-rows", type=int, default=3)
    parser.add_argument("--local", default=None, metavar="ROOT", help="Local storage root.")
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
        arguments.prefixes,
        local_root=arguments.local,
        max_distinct=arguments.max_distinct,
        sample_rows=arguments.sample_rows,
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
