"""Port abstracting how raster layers are discovered and read."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adaptation_atlas_api.domain.models.dataset import DatasetDefinition
from adaptation_atlas_api.domain.models.query import ValidatedFilters
from adaptation_atlas_api.domain.models.spatial import (
    GeographicSelection,
    PointSamples,
    RasterLayer,
    RasterStack,
    SpatialTransform,
)


class RasterReader(ABC):
    """Discovers the layers of a raster dataset and reads subsets of them."""

    @abstractmethod
    def list_layers(
        self, dataset: DatasetDefinition, filters: ValidatedFilters
    ) -> list[RasterLayer]:
        """Find every layer (file band) matching the validated filters.

        Args:
            dataset: Raster dataset.
            filters: Validated filters with partition values and file patterns.

        Returns:
            The layers in chronological order, each with its time coverage.
        """

    @abstractmethod
    def read_area(
        self,
        dataset: DatasetDefinition,
        layers: list[RasterLayer],
        selection: GeographicSelection,
        transform: SpatialTransform,
    ) -> RasterStack:
        """Read the layers clipped to an area, optionally reprojected and resampled.

        Args:
            dataset: Raster dataset.
            layers: Layers to read, in output order.
            selection: Polygon or bounding box selection in WGS84.
            transform: Reprojection/resampling to apply.

        Returns:
            The clipped stack with ``NaN`` as nodata.
        """

    @abstractmethod
    def read_points(
        self, dataset: DatasetDefinition, layers: list[RasterLayer], selection: GeographicSelection
    ) -> PointSamples:
        """Sample the layers at a set of points.

        Args:
            dataset: Raster dataset.
            layers: Layers to sample, in output order.
            selection: Point selection in WGS84.

        Returns:
            The sampled values, ``NaN`` outside the coverage.
        """
