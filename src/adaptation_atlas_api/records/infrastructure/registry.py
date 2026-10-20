"""Plugin registry of the records slice: tabular readers and exporters by catalog key."""

from __future__ import annotations

from adaptation_atlas_api.records.domain.query import OutputFormat
from adaptation_atlas_api.records.infrastructure.duckdb_reader import DuckDBTabularReader
from adaptation_atlas_api.records.ports.tabular_reader import TabularReader
from adaptation_atlas_api.shared.domain.exceptions import CatalogConfigurationError
from adaptation_atlas_api.shared.infrastructure.csv_exporter import CsvExporter
from adaptation_atlas_api.shared.ports.exporter import TabularExporter

TABULAR_READERS: dict[str, type[TabularReader]] = {
    "duckdb_parquet": DuckDBTabularReader,
}

TABULAR_EXPORTERS: dict[OutputFormat, type[TabularExporter]] = {
    OutputFormat.CSV: CsvExporter,
}


def resolve_tabular_reader(reader_key: str) -> type[TabularReader]:
    """Return the reader class registered under ``reader_key``.

    Args:
        reader_key: Value of the ``reader`` attribute in a dataset definition.

    Returns:
        The adapter class.

    Raises:
        CatalogConfigurationError: If no reader is registered under the key.
    """
    # Unknown keys are a catalog mistake that must surface at startup.
    if reader_key not in TABULAR_READERS:
        raise CatalogConfigurationError(
            f"Unknown tabular reader '{reader_key}'. Registered readers: {sorted(TABULAR_READERS)}"
        )
    return TABULAR_READERS[reader_key]


def resolve_tabular_exporter(output_format: OutputFormat) -> TabularExporter | None:
    """Return an exporter instance for ``output_format``.

    Args:
        output_format: Requested output format.

    Returns:
        A new exporter, or ``None`` when the format is served natively (JSON).
    """
    exporter_class = TABULAR_EXPORTERS.get(output_format)

    # JSON is produced by the API layer itself, so no exporter is registered for it.
    if exporter_class is None:
        return None
    return exporter_class()
