"""NetCDF exporter: a CF-style ``(time, y, x)`` variable with the processing metadata."""

from __future__ import annotations

import json
import os
import tempfile

import numpy as np
import rioxarray  # noqa: F401 - registers the ``.rio`` accessor
import xarray as xr

from adaptation_atlas_api.raster.domain.models import RasterResult
from adaptation_atlas_api.raster.ports.raster_exporter import RasterExporter


class NetCdfExporter(RasterExporter):
    """Writes a NetCDF4 file with one time step (or window) per slice."""

    media_type = "application/x-netcdf"
    file_extension = "nc"

    def export(self, result: RasterResult) -> bytes:
        """Serialize the product as NetCDF4.

        Args:
            result: An area result.

        Returns:
            The NetCDF content.
        """
        grid = result.grid
        assert grid is not None  # noqa: S101 - exporters only receive area results
        a, _, c, _, e, f = grid.transform
        # Coordinates are cell centres derived from the affine transform.
        xs = c + a * (np.arange(grid.width) + 0.5)
        ys = f + e * (np.arange(grid.height) + 0.5)
        labels = [layer.label for layer in result.output_layers]
        variable_name = result.variable.replace("-", "_")

        array = xr.DataArray(
            result.data.astype(np.float32),
            dims=("time", "y", "x"),
            coords={
                "time": labels,
                "y": ys,
                "x": xs,
                "time_start": ("time", [layer.start.isoformat() for layer in result.output_layers]),
                "time_end": ("time", [layer.end.isoformat() for layer in result.output_layers]),
                "coverage": ("time", [layer.coverage for layer in result.output_layers]),
            },
            name=variable_name,
            attrs={"units": result.unit, "long_name": result.variable, "_FillValue": np.nan},
        )
        dataset = array.to_dataset()
        dataset.attrs = {
            "dataset_id": result.dataset_id,
            "variable": result.variable,
            "unit": result.unit,
            "aggregation": result.aggregation.value,
            "atlas_metadata": json.dumps(result.describe(), default=str),
        }
        # rioxarray records the CRS and transform in CF conventions.
        if grid.crs:
            dataset = dataset.rio.write_crs(grid.crs)
        dataset[variable_name].encoding.update({"zlib": True, "complevel": 4})

        # netCDF4 writes to files only, so a temporary file is used and read back.
        handle, path = tempfile.mkstemp(suffix=".nc")
        os.close(handle)
        try:
            dataset.to_netcdf(path, engine="netcdf4", format="NETCDF4")
            with open(path, "rb") as stream:
                return stream.read()
        finally:
            os.unlink(path)
