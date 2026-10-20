"""Port turning an area raster result into a downloadable spatial file."""

from __future__ import annotations

from abc import ABC, abstractmethod

from adaptation_atlas_api.domain.models.spatial import RasterResult


class RasterExporter(ABC):
    """Serializes a :class:`RasterResult` with a grid into a spatial file format."""

    media_type: str = "application/octet-stream"
    file_extension: str = "bin"

    @abstractmethod
    def export(self, result: RasterResult) -> bytes:
        """Serialize the product.

        Args:
            result: An area result (``result.grid`` is not ``None``).

        Returns:
            The complete file content.
        """
