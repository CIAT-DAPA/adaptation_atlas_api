"""Plugin registry of the raster slice: raster readers and exporters by catalog key."""

from __future__ import annotations

from adaptation_atlas_api.raster.domain.models import RasterFormat
from adaptation_atlas_api.raster.infrastructure.geotiff_exporter import GeoTiffExporter
from adaptation_atlas_api.raster.infrastructure.netcdf_exporter import NetCdfExporter
from adaptation_atlas_api.raster.infrastructure.rasterio_reader import RasterioRasterReader
from adaptation_atlas_api.raster.ports.raster_exporter import RasterExporter
from adaptation_atlas_api.raster.ports.raster_reader import RasterReader
from adaptation_atlas_api.shared.domain.exceptions import CatalogConfigurationError

RASTER_READERS: dict[str, type[RasterReader]] = {
    "rasterio_geotiff": RasterioRasterReader,
}

RASTER_EXPORTERS: dict[RasterFormat, type[RasterExporter]] = {
    RasterFormat.GEOTIFF: GeoTiffExporter,
    RasterFormat.NETCDF: NetCdfExporter,
}


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
