"""Schemas of the dataset discovery endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adaptation_atlas_api.api.schemas.raster import RasterSchema
from adaptation_atlas_api.domain.models.dataset import (
    DatasetDefinition,
    FilterDefinition,
    ResponseFieldDefinition,
)
from adaptation_atlas_api.domain.services.catalog import DatasetAvailability


class FilterSchema(BaseModel):
    """Public description of a filter accepted by a dataset.

    Attributes:
        name: Query parameter name.
        required: Whether the parameter must always be supplied.
        allowed_values: Controlled vocabulary, when the filter has one.
        default: Value applied when the parameter is omitted.
        description: Meaning of the filter.
    """

    name: str
    required: bool
    allowed_values: list[str] | None = None
    default: str | None = None
    description: str = ""

    @classmethod
    def from_domain(cls, filter_definition: FilterDefinition) -> FilterSchema:
        """Build the schema from a domain filter definition.

        Args:
            filter_definition: Domain object.

        Returns:
            The public representation.
        """
        return cls(
            name=filter_definition.name,
            required=filter_definition.required,
            allowed_values=filter_definition.allowed_values,
            default=filter_definition.default,
            description=filter_definition.description,
        )


class FieldSchema(BaseModel):
    """Public description of a response field.

    Attributes:
        name: Field name in every record.
        type: Logical type (string, number or integer).
        description: Meaning of the field.
    """

    name: str
    type: str
    description: str = ""

    @classmethod
    def from_domain(cls, field_definition: ResponseFieldDefinition) -> FieldSchema:
        """Build the schema from a domain field definition.

        Args:
            field_definition: Domain object.

        Returns:
            The public representation.
        """
        return cls(
            name=field_definition.name,
            type=field_definition.type.value,
            description=field_definition.description,
        )


class DatasetSummary(BaseModel):
    """Short description of a dataset used in listings.

    Attributes:
        id: Public identifier.
        name: Human readable title.
        description: Longer description.
        domain: Thematic domain (climate, exposure, impacts...).
        type: Dataset kind (tabular or raster).
        output_formats: Supported export formats.
    """

    id: str
    name: str
    description: str = ""
    domain: str = ""
    type: str
    output_formats: list[str]

    @classmethod
    def from_domain(cls, dataset: DatasetDefinition) -> DatasetSummary:
        """Build the summary from a domain dataset definition.

        Args:
            dataset: Domain object.

        Returns:
            The public representation.
        """
        return cls(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            domain=dataset.domain,
            type=dataset.type.value,
            output_formats=list(dataset.output_formats),
        )


class DatasetDetail(DatasetSummary):
    """Complete public description of a dataset.

    Attributes:
        filters: Accepted query parameters with their vocabularies.
        fields: Fields returned for every record. Empty when the dataset returns every
            source column as is.
        available_partition_values: Partition values found in storage (when discovery is on).
        available_combinations: Partition combinations found in storage.
        example_query: Copy-ready records URL using the required filters (tabular) or the
            raster endpoint URL (raster).
        example_filters: The required filters as a JSON object for the ``filters`` parameter.
        example_request: Example body for ``POST /datasets/{dataset_id}/raster`` (raster only).
        raster: Spatial/temporal description (raster datasets only).
        metadata: Extra metadata, including source metadata sidecars when enabled.
    """

    filters: list[FilterSchema] = Field(default_factory=list)
    fields: list[FieldSchema] = Field(default_factory=list)
    available_partition_values: dict[str, list[str]] = Field(default_factory=dict)
    available_combinations: list[dict[str, str]] = Field(default_factory=list)
    example_query: str = Field(
        default="",
        examples=["/api/v1/datasets/hazard_exposure/records?country=KEN&period=historic"],
    )
    example_filters: dict[str, str] = Field(
        default_factory=dict, examples=[{"country": "KEN", "period": "historic"}]
    )
    example_request: dict[str, Any] = Field(default_factory=dict)
    raster: RasterSchema | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_availability(cls, availability: DatasetAvailability) -> DatasetDetail:
        """Build the detail view from the catalog service output.

        Args:
            availability: Dataset definition plus storage availability.

        Returns:
            The public representation.
        """
        dataset = availability.dataset
        return cls(
            id=dataset.id,
            name=dataset.name,
            description=dataset.description,
            domain=dataset.domain,
            type=dataset.type.value,
            output_formats=list(dataset.output_formats),
            filters=[FilterSchema.from_domain(item) for item in dataset.filters],
            fields=[FieldSchema.from_domain(item) for item in dataset.response_fields],
            available_partition_values=availability.available_partition_values,
            available_combinations=availability.available_combinations,
            example_query=availability.example_query,
            example_filters=availability.example_filters,
            example_request=availability.example_request,
            raster=RasterSchema.from_domain(dataset.raster) if dataset.raster else None,
            metadata=dict(dataset.metadata),
        )
