"""Schemas of the dataset discovery endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adaptation_atlas_api.catalog.application.catalog_service import DatasetAvailability
from adaptation_atlas_api.shared.domain.dataset import (
    DatasetDefinition,
    FilterDefinition,
    RasterDefinition,
    ResponseFieldDefinition,
    VariableDefinition,
)


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


class VariableSchema(BaseModel):
    """Public description of a raster variable.

    Attributes:
        name: Variable value used in the variable filter.
        unit: Unit of the stored values.
        available_units: Units a client may request (source unit plus conversions).
        aggregation: Rule applied when aggregating over time.
        description: Meaning of the variable.
    """

    name: str
    unit: str
    available_units: list[str]
    aggregation: str
    description: str = ""

    @classmethod
    def from_domain(cls, name: str, definition: VariableDefinition) -> VariableSchema:
        """Build the schema from a domain variable definition.

        Args:
            name: Variable value.
            definition: Domain object.

        Returns:
            The public representation.
        """
        return cls(
            name=name,
            unit=definition.unit,
            available_units=definition.available_units,
            aggregation=definition.aggregation.value,
            description=definition.description,
        )


class RasterSchema(BaseModel):
    """Public spatial/temporal description of a raster dataset.

    Attributes:
        crs: Coordinate reference system of the source.
        resolution: Source pixel size in CRS units.
        coverage: Source extent ``[min_x, min_y, max_x, max_y]``.
        temporal_resolution: Time step of the source layers.
        time_range_required: Whether ``start_date``/``end_date`` are mandatory.
        variables: Variables with their units and aggregation rules.
        allowed_crs: Target CRS clients may request (``null`` means any).
        allowed_resolutions: Target resolutions clients may request (``null`` means any).
        resampling_methods: Resampling methods clients may request.
        default_resampling: Method applied when none is requested.
        aggregations: Temporal aggregation types supported.
    """

    crs: str
    resolution: float
    coverage: list[float] | None = None
    temporal_resolution: str
    time_range_required: bool
    variables: list[VariableSchema]
    allowed_crs: list[str] | None = None
    allowed_resolutions: list[float] | None = None
    resampling_methods: list[str]
    default_resampling: str
    aggregations: list[str]

    @classmethod
    def from_domain(cls, raster: RasterDefinition) -> RasterSchema:
        """Build the schema from a domain raster definition.

        Args:
            raster: Domain object.

        Returns:
            The public representation.
        """
        return cls(
            crs=raster.crs,
            resolution=raster.resolution,
            coverage=raster.coverage,
            temporal_resolution=raster.temporal_resolution.value,
            time_range_required=raster.time_range_required,
            variables=[
                VariableSchema.from_domain(name, definition)
                for name, definition in raster.variables.items()
            ],
            allowed_crs=raster.allowed_crs,
            allowed_resolutions=raster.allowed_resolutions,
            resampling_methods=list(raster.resampling_methods),
            default_resampling=raster.default_resampling,
            aggregations=list(raster.aggregations),
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
