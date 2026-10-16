"""Schemas of the dataset discovery endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

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
        type: Dataset kind (tabular or raster).
        output_formats: Supported export formats.
    """

    id: str
    name: str
    description: str = ""
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
            type=dataset.type.value,
            output_formats=list(dataset.output_formats),
        )


class DatasetDetail(DatasetSummary):
    """Complete public description of a dataset.

    Attributes:
        filters: Accepted query parameters with their vocabularies.
        fields: Fields returned for every record.
        available_partition_values: Partition values found in storage (when discovery is on).
        available_combinations: Partition combinations found in storage.
        metadata: Extra metadata, including source metadata sidecars when enabled.
    """

    filters: list[FilterSchema] = Field(default_factory=list)
    fields: list[FieldSchema] = Field(default_factory=list)
    available_partition_values: dict[str, list[str]] = Field(default_factory=dict)
    available_combinations: list[dict[str, str]] = Field(default_factory=list)
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
            type=dataset.type.value,
            output_formats=list(dataset.output_formats),
            filters=[FilterSchema.from_domain(item) for item in dataset.filters],
            fields=[FieldSchema.from_domain(item) for item in dataset.response_fields],
            available_partition_values=availability.available_partition_values,
            available_combinations=availability.available_combinations,
            metadata=dict(dataset.metadata),
        )
