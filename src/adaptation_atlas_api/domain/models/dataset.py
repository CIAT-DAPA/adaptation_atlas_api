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
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_consistency(self) -> DatasetDefinition:
        """Validate cross-field consistency of the definition.

        Returns:
            The validated dataset definition.

        Raises:
            ValueError: When a partition filter targets an unknown partition, a file
                pattern filter targets an unknown pattern, or the path template references
                an unknown placeholder.
        """
        partition_names = {partition.name for partition in self.partitions}
        pattern_keys = set(self.storage.file_patterns)

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
