"""Ports: abstract interfaces the domain depends on and adapters implement."""

from adaptation_atlas_api.ports.catalog_repository import CatalogRepository
from adaptation_atlas_api.ports.exporter import TabularExporter
from adaptation_atlas_api.ports.partition_index import PartitionIndex
from adaptation_atlas_api.ports.storage import Storage
from adaptation_atlas_api.ports.tabular_reader import TabularReader

__all__ = ["CatalogRepository", "PartitionIndex", "Storage", "TabularExporter", "TabularReader"]
