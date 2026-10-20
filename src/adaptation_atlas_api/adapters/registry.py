"""Plugin registry mapping catalog keys to concrete adapters.

The catalog declares, for every dataset, the ``reader`` able to read it. This
module resolves those keys to adapter classes so new readers can be added
without touching the domain or the API layer.
"""

from __future__ import annotations

from collections.abc import Callable

from adaptation_atlas_api.adapters.exporters.csv_exporter import CsvExporter
from adaptation_atlas_api.adapters.exporters.geotiff_exporter import GeoTiffExporter
from adaptation_atlas_api.adapters.exporters.netcdf_exporter import NetCdfExporter
from adaptation_atlas_api.adapters.raster.rasterio_reader import RasterioRasterReader
from adaptation_atlas_api.adapters.tabular.duckdb_reader import DuckDBTabularReader
from adaptation_atlas_api.domain.exceptions import CatalogConfigurationError
from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.models.query import OutputFormat
from adaptation_atlas_api.domain.models.spatial import RasterFormat
from adaptation_atlas_api.ports.exporter import TabularExporter
from adaptation_atlas_api.ports.raster_exporter import RasterExporter
from adaptation_atlas_api.ports.raster_reader import RasterReader
from adaptation_atlas_api.ports.storage import Storage
from adaptation_atlas_api.ports.tabular_reader import TabularReader

TabularReaderFactory = Callable[[Storage], TabularReader]

TABULAR_READERS: dict[str, type[TabularReader]] = {
    "duckdb_parquet": DuckDBTabularReader,
}

TABULAR_EXPORTERS: dict[OutputFormat, type[TabularExporter]] = {
    OutputFormat.CSV: CsvExporter,
}

RASTER_READERS: dict[str, type[RasterReader]] = {
    "rasterio_geotiff": RasterioRasterReader,
}

RASTER_EXPORTERS: dict[RasterFormat, type[RasterExporter]] = {
    RasterFormat.GEOTIFF: GeoTiffExporter,
    RasterFormat.NETCDF: NetCdfExporter,
}


def resolve_reader(dataset: DatasetDefinition) -> type[TabularReader] | type[RasterReader]:
    """Return the reader class able to serve ``dataset`` according to its type.

    Args:
        dataset: Dataset definition.

    Returns:
        A tabular reader class for tabular datasets, a raster reader class otherwise.
    """
    # The dataset type decides which registry the key is looked up in.
    if dataset.is_raster:
        return resolve_raster_reader(dataset.reader)
    return resolve_tabular_reader(dataset.reader)


def resolve_raster_reader(reader_key: str) -> type[RasterReader]:
    """Return the raster reader class registered under ``reader_key``.

    Args:
        reader_key: Value of the ``reader`` attribute in a raster dataset definition.

    Returns:
        The adapter class.

    Raises:
        CatalogConfigurationError: If no raster reader is registered under the key.
    """
    # Unknown keys are a catalog mistake that must surface at startup.
    if reader_key not in RASTER_READERS:
        raise CatalogConfigurationError(
            f"Unknown raster reader '{reader_key}'. Registered readers: {sorted(RASTER_READERS)}"
        )
    return RASTER_READERS[reader_key]


def resolve_raster_exporter(output_format: RasterFormat) -> RasterExporter:
    """Return an exporter instance for a spatial output format.

    Args:
        output_format: Requested raster format (GeoTIFF or NetCDF).

    Returns:
        A new exporter.

    Raises:
        CatalogConfigurationError: If no exporter handles the format.
    """
    exporter_class = RASTER_EXPORTERS.get(output_format)
    # Point formats (json/csv) are served by the tabular machinery, never here.
    if exporter_class is None:
        raise CatalogConfigurationError(f"No raster exporter for format '{output_format.value}'.")
    return exporter_class()


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
