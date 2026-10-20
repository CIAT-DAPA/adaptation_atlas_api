"""Models that describe a published dataset as registered in the catalog.

A dataset definition is the contract between the catalog (YAML files), the
validation rules and the storage adapters. It describes where the data lives,
how it is partitioned, which filters a client may use and which fields are
returned.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def extract_placeholders(template: str) -> list[str]:
    """Extract the ``{name}`` placeholders used in a template string.

    Args:
        template: Path or file name template.

    Returns:
        Placeholder names in order of appearance, without braces.
    """
    placeholders: list[str] = []
    start = template.find("{")

    # Walk the template collecting every ``{name}`` segment.
    while start != -1:
        end = template.find("}", start)
        # An opening brace without a closing one ends the scan.
        if end == -1:
            break
        placeholders.append(template[start + 1 : end])
        start = template.find("{", end)
    return placeholders


class DatasetType(str, Enum):
    """Kind of product exposed by a dataset."""

    TABULAR = "tabular"
    RASTER = "raster"


class FilterSource(str, Enum):
    """Where a filter is resolved.

    * ``partition``: the value selects a Hive partition folder in the path template.
    * ``column``: the value is compared against a column inside the Parquet files.
    * ``file_pattern``: the value selects which file glob is read inside the leaf folder.
    * ``derived``: the value is translated by a registered rule (e.g. ``admin_level``).
    """

    PARTITION = "partition"
    COLUMN = "column"
    FILE_PATTERN = "file_pattern"
    DERIVED = "derived"


class FieldType(str, Enum):
    """Logical type used to cast and document a response field."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"


class PartitionDefinition(BaseModel):
    """A Hive partition key present in the dataset path template.

    Attributes:
        name: Partition key as it appears in the path (``key=value``).
        allowed_values: Known values for the key; ``None`` means discovered at runtime.
        description: Human readable meaning of the partition.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    allowed_values: list[str] | None = None
    description: str = ""


class StorageDefinition(BaseModel):
    """Describes how the dataset files are laid out in the storage backend.

    Attributes:
        path_template: Path relative to the bucket root with ``{partition}`` placeholders.
            Every placeholder must match a registered partition or the file pattern
            placeholder ``{file}``.
        file_patterns: Mapping between a public ``file_pattern`` filter value and the glob
            used to select files inside the leaf folder (e.g. ``interaction -> int*.parquet``).
        default_file_pattern: Key of ``file_patterns`` used when the client sends nothing.
        discover_partitions: When ``True`` the storage is listed to build an index of the
            partition combinations that actually exist.
        metadata_sidecar: When ``True`` the ``<file>.json`` sidecars found next to the
            Parquet files are read to enrich the dataset description.
    """

    model_config = ConfigDict(frozen=True)

    path_template: str
    file_patterns: dict[str, str] = Field(default_factory=lambda: {"default": "*.parquet"})
    default_file_pattern: str = "default"
    discover_partitions: bool = False
    metadata_sidecar: bool = False

    @property
    def static_prefix(self) -> str:
        """Return the directory prefix of the template that contains no placeholder.

        Returns:
            The template up to (and including) the last ``/`` before the first ``{``,
            suitable for listing a storage prefix.
        """
        head = self.path_template.split("{", 1)[0]
        # Cut at the last directory separator so the prefix is a complete folder path.
        return head[: head.rfind("/") + 1] if "/" in head else ""

    @model_validator(mode="after")
    def _check_default_pattern(self) -> StorageDefinition:
        """Ensure the default file pattern key exists in ``file_patterns``.

        Returns:
            The validated storage definition.

        Raises:
            ValueError: If ``default_file_pattern`` is not a key of ``file_patterns``.
        """
        # The default pattern must be one of the registered patterns.
        if self.default_file_pattern not in self.file_patterns:
            raise ValueError(
                f"default_file_pattern '{self.default_file_pattern}' is not defined in "
                f"file_patterns {sorted(self.file_patterns)}"
            )
        return self


class FilterDefinition(BaseModel):
    """A query parameter that clients may use to filter a dataset.

    Attributes:
        name: Public parameter name exposed by the API.
        source: Where the filter is resolved (partition, column, file pattern or derived).
        target: Partition key, column name or rule identifier the filter maps to.
            Defaults to ``name`` when omitted.
        required: Whether the client must always supply the filter.
        allowed_values: Controlled vocabulary; ``None`` means any value is accepted.
        case_insensitive: Whether values are matched ignoring case and normalized to the
            canonical spelling from ``allowed_values``.
        multiple: Whether the client may send several values (repeated parameter, comma
            separated list or JSON array); they are combined with OR.
        default: Value applied when the client omits the filter.
        description: Human readable description shown in the API documentation.
        example: Example value shown in the API documentation.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    source: FilterSource
    target: str | None = None
    required: bool = False
    allowed_values: list[str] | None = None
    case_insensitive: bool = False
    multiple: bool = True
    default: str | None = None
    description: str = ""
    example: str | None = None

    @property
    def resolved_target(self) -> str:
        """Return the storage target of the filter.

        Returns:
            ``target`` when defined, otherwise the public ``name``.
        """
        return self.target or self.name


class TemporalResolution(str, Enum):
    """Time step of every layer in a raster dataset."""

    DAILY = "daily"
    MONTHLY = "monthly"
    ANNUAL = "annual"


class AggregationMethod(str, Enum):
    """Rule used to combine the layers of a variable over a temporal window."""

    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"


class UnitConversionDefinition(BaseModel):
    """A registered linear conversion from the source unit to a target unit.

    ``target = source * factor + offset``. Conversions are never inferred: only the
    ones declared here are offered to clients.

    Attributes:
        factor: Multiplicative factor.
        offset: Additive offset applied after the factor.
        description: Human readable description of the conversion.
    """

    model_config = ConfigDict(frozen=True)

    factor: float = 1.0
    offset: float = 0.0
    description: str = ""


class VariableDefinition(BaseModel):
    """Physical meaning of a raster variable and the operations it supports.

    Attributes:
        unit: Unit of the stored values.
        aggregation: Rule used to aggregate the variable over time (sum for totals such
            as precipitation, mean for states such as temperature or indices).
        description: Human readable description.
        conversions: Target unit -> conversion applied when a client asks for that unit.
    """

    model_config = ConfigDict(frozen=True)

    unit: str
    aggregation: AggregationMethod = AggregationMethod.MEAN
    description: str = ""
    conversions: dict[str, UnitConversionDefinition] = Field(default_factory=dict)

    @property
    def available_units(self) -> list[str]:
        """Return the units a client may request for the variable.

        Returns:
            The source unit followed by every registered conversion target.
        """
        return [self.unit, *self.conversions]


class TimeAxisDefinition(BaseModel):
    """How the date of every raster layer is recovered from storage.

    Date components (``{year}``, ``{month}``, ``{day}``) may come from the file name,
    from the band descriptions inside each file, or from both (e.g. yearly files holding
    one band per month).

    Attributes:
        file_pattern: File name pattern with placeholders (also partition placeholders
            such as ``{variable}``); ``None`` when the file name carries no date.
        band_pattern: Pattern matched against band descriptions; ``None`` for single
            band files.
    """

    model_config = ConfigDict(frozen=True)

    file_pattern: str | None = None
    band_pattern: str | None = None

    @property
    def date_placeholders(self) -> set[str]:
        """Return the date components provided by the patterns.

        Returns:
            Subset of ``{"year", "month", "day"}`` found in both patterns.
        """
        found: set[str] = set()
        # Collect the date components from both patterns.
        for pattern in (self.file_pattern, self.band_pattern):
            # Missing patterns contribute nothing.
            if pattern is None:
                continue
            found.update(name for name in extract_placeholders(pattern) if name in DATE_PARTS)
        return found


DATE_PARTS = ("year", "month", "day")

# Date components every temporal resolution needs to place a layer in time.
REQUIRED_DATE_PARTS: dict[TemporalResolution, set[str]] = {
    TemporalResolution.ANNUAL: {"year"},
    TemporalResolution.MONTHLY: {"year", "month"},
    TemporalResolution.DAILY: {"year", "month", "day"},
}


class RasterDefinition(BaseModel):
    """Spatial and temporal characteristics of a raster dataset.

    Attributes:
        crs: Coordinate reference system of the source files (e.g. ``EPSG:4326``).
        resolution: Pixel size of the source files in CRS units.
        coverage: Geographic extent ``[min_x, min_y, max_x, max_y]`` in the source CRS.
        temporal_resolution: Time step of every layer.
        time_axis: How layer dates are recovered from file names and band descriptions.
        variable_filter: Name of the filter that selects the variable.
        variables: Variable value -> physical description and supported operations.
        allowed_crs: Target coordinate reference systems clients may request. ``None``
            accepts any CRS the engine understands.
        allowed_resolutions: Target resolutions (in target CRS units) clients may request.
            ``None`` accepts any positive resolution.
        resampling_methods: Resampling methods clients may request.
        default_resampling: Method applied when a resolution or CRS is requested without
            a method.
        aggregations: Temporal aggregation types supported by the dataset.
        time_range_required: Whether ``start_date``/``end_date`` must be supplied.
    """

    model_config = ConfigDict(frozen=True)

    crs: str = "EPSG:4326"
    resolution: float = Field(gt=0)
    coverage: list[float] | None = None
    temporal_resolution: TemporalResolution
    time_axis: TimeAxisDefinition
    variable_filter: str = "variable"
    variables: dict[str, VariableDefinition] = Field(default_factory=dict)
    allowed_crs: list[str] | None = Field(default_factory=lambda: ["EPSG:4326"])
    allowed_resolutions: list[float] | None = None
    resampling_methods: list[str] = Field(
        default_factory=lambda: ["nearest", "bilinear", "cubic", "average"]
    )
    default_resampling: str = "bilinear"
    aggregations: list[str] = Field(
        default_factory=lambda: [
            "none",
            "period",
            "calendar_month",
            "fixed_window",
            "crop_season",
            "stages",
        ]
    )
    time_range_required: bool = True

    @model_validator(mode="after")
    def _check_consistency(self) -> RasterDefinition:
        """Validate the raster definition.

        Returns:
            The validated definition.

        Raises:
            ValueError: When the time axis does not provide the components required by
                the temporal resolution, the default resampling method is not allowed,
                or the coverage is malformed.
        """
        required = REQUIRED_DATE_PARTS[self.temporal_resolution]
        provided = self.time_axis.date_placeholders
        # Every date component the resolution needs must come from some pattern.
        if not required.issubset(provided):
            raise ValueError(
                f"time_axis must provide {sorted(required)} for a {self.temporal_resolution.value}"
                f" dataset, found {sorted(provided)}"
            )
        # The default resampling method must be one clients may also request.
        if self.default_resampling not in self.resampling_methods:
            raise ValueError(
                f"default_resampling '{self.default_resampling}' is not in resampling_methods"
            )
        # A coverage box needs exactly four ordered coordinates.
        if self.coverage is not None and (
            len(self.coverage) != 4
            or self.coverage[0] >= self.coverage[2]
            or self.coverage[1] >= self.coverage[3]
        ):
            raise ValueError("coverage must be [min_x, min_y, max_x, max_y]")
        return self


class ResponseFieldDefinition(BaseModel):
    """A field included in every record returned for the dataset.

    Attributes:
        name: Public field name in the response.
        source: Parquet column or partition key the field is read from (defaults to ``name``).
        type: Logical type used for casting and documentation.
        description: Human readable description of the field.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    source: str | None = None
    type: FieldType = FieldType.STRING
    description: str = ""

    @property
    def resolved_source(self) -> str:
        """Return the column the field is read from.

        Returns:
            ``source`` when defined, otherwise the public ``name``.
        """
        return self.source or self.name


class DatasetDefinition(BaseModel):
    """Complete description of a published dataset.

    Attributes:
        id: Public identifier used in URLs.
        name: Human readable title.
        description: Longer description shown in the catalog endpoints.
        domain: Thematic domain the dataset belongs to (climate, exposure, impacts...).
        type: Dataset kind (tabular or raster).
        reader: Key of the adapter able to read the dataset (e.g. ``duckdb_parquet``).
        storage: Layout of the files in the storage backend.
        partitions: Hive partition keys used in the path template.
        filters: Query parameters accepted by the dataset.
        response_fields: Fields returned for each record. When empty, every column found
            in the source files is returned with its native type.
        excluded_columns: Source columns never returned when fields are automatic (e.g.
            geometry blobs that cannot be serialized to JSON or CSV).
        order_by: Public field names used to sort results so pagination is stable.
        output_formats: Export formats supported by the dataset.
        raster: Spatial/temporal description, required for raster datasets.
        metadata: Free-form extra metadata (e.g. loaded from sidecar files).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""
    domain: str = ""
    type: DatasetType = DatasetType.TABULAR
    reader: str = "duckdb_parquet"
    storage: StorageDefinition
    partitions: list[PartitionDefinition] = Field(default_factory=list)
    filters: list[FilterDefinition] = Field(default_factory=list)
    response_fields: list[ResponseFieldDefinition] = Field(default_factory=list)
    excluded_columns: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    output_formats: list[str] = Field(default_factory=lambda: ["json", "csv"])
    raster: RasterDefinition | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_consistency(self) -> DatasetDefinition:
        """Validate cross-field consistency of the definition.

        Returns:
            The validated dataset definition.

        Raises:
            ValueError: When a partition filter targets an unknown partition, a file
                pattern filter targets an unknown pattern, the path template references
                an unknown placeholder, or the raster block is inconsistent with the type.
        """
        partition_names = {partition.name for partition in self.partitions}
        pattern_keys = set(self.storage.file_patterns)

        # Raster datasets need their spatial description; tabular ones must not carry it.
        if self.type is DatasetType.RASTER and self.raster is None:
            raise ValueError("a dataset of type 'raster' must declare the 'raster' block")
        if self.type is DatasetType.TABULAR and self.raster is not None:
            raise ValueError("a dataset of type 'tabular' cannot declare the 'raster' block")
        # The variable filter of a raster dataset must exist and only allow known variables.
        if self.raster is not None:
            variable_filter = self.get_filter(self.raster.variable_filter)
            if variable_filter is None:
                raise ValueError(
                    f"raster.variable_filter '{self.raster.variable_filter}' is not a filter"
                )
            unknown = set(variable_filter.allowed_values or []) - set(self.raster.variables)
            if unknown or not variable_filter.allowed_values:
                raise ValueError(
                    f"filter '{variable_filter.name}' must list only variables declared in "
                    f"raster.variables; unknown: {sorted(unknown)}"
                )

        # Every filter must point to something that exists in the definition.
        for filter_definition in self.filters:
            # Partition filters must reference a declared partition key.
            if (
                filter_definition.source is FilterSource.PARTITION
                and filter_definition.resolved_target not in partition_names
            ):
                raise ValueError(
                    f"filter '{filter_definition.name}' targets unknown partition "
                    f"'{filter_definition.resolved_target}'"
                )
            # File pattern filters must only allow values that are registered patterns.
            if filter_definition.source is FilterSource.FILE_PATTERN:
                allowed = set(filter_definition.allowed_values or pattern_keys)
                unknown = allowed - pattern_keys
                # Any allowed value without a glob would make the query impossible.
                if unknown:
                    raise ValueError(
                        f"filter '{filter_definition.name}' allows file patterns "
                        f"{sorted(unknown)} that are not defined in storage.file_patterns"
                    )

        # Every placeholder in the template must be a partition or the file placeholder.
        for placeholder in self.template_placeholders():
            # ``{file}`` is reserved for the file pattern; everything else is a partition.
            if placeholder != "file" and placeholder not in partition_names:
                raise ValueError(
                    f"path_template placeholder '{{{placeholder}}}' is not a declared partition"
                )

        # Placeholders inside file patterns must also be declared partitions.
        for key, pattern in self.storage.file_patterns.items():
            for placeholder in extract_placeholders(pattern):
                # The file placeholder cannot be nested inside a file pattern.
                if placeholder == "file" or placeholder not in partition_names:
                    raise ValueError(
                        f"file pattern '{key}' placeholder '{{{placeholder}}}' is not a "
                        "declared partition"
                    )

        # Sorting can only use fields that are part of an explicit response definition.
        if self.response_fields:
            field_names = {item.name for item in self.response_fields}
            for order_field in self.order_by:
                # An unknown sort field would make the query fail at runtime.
                if order_field not in field_names:
                    raise ValueError(f"order_by field '{order_field}' is not a response field")
        return self

    def template_placeholders(self) -> list[str]:
        """Extract the placeholder names used in the storage path template.

        Returns:
            Placeholder names in order of appearance, without braces.
        """
        return extract_placeholders(self.storage.path_template)

    @property
    def is_raster(self) -> bool:
        """Tell whether the dataset is served by the raster endpoint.

        Returns:
            ``True`` for raster datasets.
        """
        return self.type is DatasetType.RASTER

    @property
    def has_explicit_fields(self) -> bool:
        """Tell whether the dataset declares its response fields explicitly.

        Returns:
            ``True`` when ``response_fields`` is not empty.
        """
        return bool(self.response_fields)

    def get_filter(self, name: str) -> FilterDefinition | None:
        """Look up a filter by its public name.

        Args:
            name: Public filter name.

        Returns:
            The matching filter definition, or ``None`` when not registered.
        """
        # Linear scan is fine: datasets expose a handful of filters.
        for filter_definition in self.filters:
            # Compare public names exactly; parameter names are case sensitive.
            if filter_definition.name == name:
                return filter_definition
        return None

    def get_partition(self, name: str) -> PartitionDefinition | None:
        """Look up a partition by its key name.

        Args:
            name: Partition key.

        Returns:
            The matching partition definition, or ``None`` when not registered.
        """
        # Linear scan over the few declared partitions.
        for partition in self.partitions:
            # Partition keys must match exactly as they appear in the path.
            if partition.name == name:
                return partition
        return None

    @property
    def filter_names(self) -> list[str]:
        """Return the public names of every registered filter.

        Returns:
            Filter names in catalog order.
        """
        return [filter_definition.name for filter_definition in self.filters]

    @property
    def required_filters(self) -> list[FilterDefinition]:
        """Return the filters that must always be supplied.

        Returns:
            Required filter definitions in catalog order.
        """
        return [
            filter_definition for filter_definition in self.filters if filter_definition.required
        ]
