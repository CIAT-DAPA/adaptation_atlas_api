"""GeoTIFF exporter: one band per output slice, metadata embedded as tags."""

from __future__ import annotations

import json

import numpy as np
import rasterio
from rasterio.io import MemoryFile

from adaptation_atlas_api.raster.domain.models import RasterResult
from adaptation_atlas_api.raster.ports.raster_exporter import RasterExporter


class GeoTiffExporter(RasterExporter):
    """Writes a tiled, compressed multi-band GeoTIFF with the processing metadata."""

    media_type = "image/tiff"
    file_extension = "tif"

    def export(self, result: RasterResult) -> bytes:
        """Serialize the product as GeoTIFF.

        Args:
            result: An area result.

        Returns:
            The GeoTIFF content.
        """
        grid = result.grid
        assert grid is not None  # noqa: S101 - exporters only receive area results
        data = result.data.astype(np.float32)
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": data.shape[0],
            "height": grid.height,
            "width": grid.width,
            "crs": grid.crs or None,
            "transform": rasterio.Affine(*grid.transform),
            "nodata": np.nan,
            "compress": "deflate",
            "tiled": grid.width >= 256 and grid.height >= 256,
        }
        metadata = result.describe()

        with MemoryFile() as memory:
            with memory.open(**profile) as target:
                target.write(data)
                target.update_tags(
                    ATLAS_METADATA=json.dumps(metadata, default=str),
                    DATASET_ID=result.dataset_id,
                    VARIABLE=result.variable,
                    UNIT=result.unit,
                    AGGREGATION=result.aggregation.value,
                )
                # Every band is labelled with its period so clients can identify slices.
                for position, layer in enumerate(result.output_layers, start=1):
                    target.set_band_description(position, layer.label)
                    target.update_tags(
                        position,
                        START=layer.start.isoformat(),
                        END=layer.end.isoformat(),
                        COVERAGE=f"{layer.coverage:.4f}",
                    )
            return bytes(memory.read())
