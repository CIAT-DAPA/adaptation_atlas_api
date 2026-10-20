"""Generic use case: retrieve a spatial product from any raster dataset in the catalog.

One pipeline serves both observed climate data (HU-004) and climate change scenarios
(HU-003): resolve the layers, clip or sample them, reproject/resample, convert units
and aggregate over time. Every step is optional and is driven by the request and the
dataset's catalog entry, so no step is specific to one user story.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping
from typing import Any

import numpy as np

from adaptation_atlas_api.raster.domain import temporal_windows, unit_conversion
from adaptation_atlas_api.raster.domain.models import (
    AREA_FORMATS,
    POINT_FORMATS,
    AggregationType,
    OutputLayer,
    ProcessingStep,
    RasterFormat,
    RasterLayer,
    RasterQuery,
    RasterResult,
)
from adaptation_atlas_api.raster.domain.spatial_validation import (
    validate_selection,
    validate_transform,
)
from adaptation_atlas_api.raster.domain.temporal_validation import (
    build_aggregation,
    validate_period,
)
from adaptation_atlas_api.raster.ports.raster_reader import RasterReader
from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition, RasterDefinition
from adaptation_atlas_api.shared.domain.exceptions import (
    DatasetNotFoundError,
    PeriodOutsideCoverageError,
    RequestTooLargeError,
    UnsupportedCombinationError,
    UnsupportedFormatError,
    WrongDatasetTypeError,
)
from adaptation_atlas_api.shared.domain.filter_validation import validate_filters
from adaptation_atlas_api.shared.ports.catalog_repository import CatalogRepository

logger = logging.getLogger(__name__)

# Request keys that are never dataset filters.
CONTROL_PARAMETERS = {
    "format",
    "start_date",
    "end_date",
    "geometry",
    "bbox",
    "points",
    "target_crs",
    "resolution",
    "resampling",
    "unit",
    "aggregation",
    "filters",
}


class RasterDatasetService:
    """Use case behind ``POST /datasets/{dataset_id}/raster``."""

    def __init__(
        self,
        catalog: CatalogRepository,
        reader: RasterReader,
        *,
        max_pixels: int,
        max_layers: int,
        max_points: int,
    ) -> None:
        """Create the service.

        Args:
            catalog: Repository holding the dataset definitions.
            reader: Adapter able to discover and read raster layers.
            max_pixels: Maximum number of cells (layers x rows x columns) read per request.
            max_layers: Maximum number of source layers read per request.
            max_points: Maximum number of points sampled per request.
        """
        self._catalog = catalog
        self._reader = reader
        self._max_pixels = max_pixels
        self._max_layers = max_layers
        self._max_points = max_points

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def resolve_dataset(self, dataset_id: str) -> DatasetDefinition:
        """Find a raster dataset by its public identifier.

        Args:
            dataset_id: Public dataset identifier.

        Returns:
            The dataset definition.

        Raises:
            DatasetNotFoundError: If the identifier is unknown.
            WrongDatasetTypeError: If the dataset is tabular (served by ``/records``).
        """
        dataset = self._catalog.get(dataset_id)
        raster_ids = [item.id for item in self._catalog.list_all() if item.is_raster]

        # Unknown identifiers list the raster datasets this endpoint can serve.
        if dataset is None:
            raise DatasetNotFoundError(
                f"Dataset '{dataset_id}' is not registered.",
                parameter="dataset_id",
                allowed_values=raster_ids,
            )
        # Tabular datasets exist but belong to the records endpoint.
        if not dataset.is_raster:
            raise WrongDatasetTypeError(
                f"Dataset '{dataset_id}' is tabular; use GET /datasets/{dataset_id}/records.",
                parameter="dataset_id",
                allowed_values=raster_ids,
            )
        return dataset

    def build_query(self, dataset_id: str, request: Mapping[str, Any]) -> RasterQuery:
        """Validate every part of the request and build the query to execute.

        Args:
            dataset_id: Public dataset identifier.
            request: Request body as a mapping (filters, period, selection, transform,
                unit, aggregation and format).

        Returns:
            A validated :class:`RasterQuery`.
        """
        dataset = self.resolve_dataset(dataset_id)
        raster = dataset.raster
        assert raster is not None  # noqa: S101 - guaranteed by resolve_dataset
        output_format = self._resolve_format(dataset, request.get("format"))
        raw_filters = dict(request.get("filters") or {})
        validated = validate_filters(dataset, raw_filters, reserved_parameters=CONTROL_PARAMETERS)
        variable = self._resolve_variable(raster, validated.applied)
        definition = raster.variables[variable]
        selection = validate_selection(
            request.get("geometry"),
            request.get("bbox"),
            request.get("points"),
            max_points=self._max_points,
        )
        # Area selections export spatial files; points export tables.
        expected = AREA_FORMATS if selection.is_area else POINT_FORMATS
        if output_format not in expected:
            raise UnsupportedFormatError(
                f"Format '{output_format.value}' cannot be used with a "
                f"{'n area' if selection.is_area else ' point'} selection.",
                parameter="format",
                allowed_values=[
                    item.value for item in expected if item.value in dataset.output_formats
                ],
            )
        transform = validate_transform(
            raster, request.get("target_crs"), request.get("resolution"), request.get("resampling")
        )
        start, end = validate_period(raster, request.get("start_date"), request.get("end_date"))
        target_unit = unit_conversion.resolve_target_unit(variable, definition, request.get("unit"))
        aggregation = build_aggregation(raster, request.get("aggregation"), start, end)
        return RasterQuery(
            dataset=dataset,
            filters=validated,
            variable=variable,
            variable_definition=definition,
            start_date=start,
            end_date=end,
            selection=selection,
            transform=transform,
            target_unit=target_unit,
            aggregation=aggregation,
            output_format=output_format,
        )

    @staticmethod
    def _resolve_format(dataset: DatasetDefinition, value: Any) -> RasterFormat:
        """Validate the requested export format.

        Args:
            dataset: Dataset being queried.
            value: Raw format name.

        Returns:
            The matching :class:`RasterFormat`.

        Raises:
            UnsupportedFormatError: If the format is unknown or disabled for the dataset.
        """
        normalized = str(value or RasterFormat.GEOTIFF.value).strip().lower()
        # The format must be known and enabled for the dataset.
        if normalized not in {item.value for item in RasterFormat} or (
            normalized not in dataset.output_formats
        ):
            raise UnsupportedFormatError(
                f"Format '{value}' is not supported by dataset '{dataset.id}'.",
                parameter="format",
                allowed_values=dataset.output_formats,
            )
        return RasterFormat(normalized)

    @staticmethod
    def _resolve_variable(raster: RasterDefinition, applied: Mapping[str, str | list[str]]) -> str:
        """Read the single variable selected by the filters.

        Args:
            raster: Raster description of the dataset.
            applied: Filters applied, by public name.

        Returns:
            The variable value.

        Raises:
            UnsupportedCombinationError: If several variables were requested at once.
        """
        value = applied.get(raster.variable_filter)
        # The validator guarantees the variable filter is present (it is required).
        values = value if isinstance(value, list) else [str(value)]
        # A raster product holds one variable; several would need several products.
        if len(values) != 1:
            raise UnsupportedCombinationError(
                "A raster request selects exactly one variable.",
                parameter=raster.variable_filter,
                allowed_values=list(raster.variables),
            )
        return values[0]

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def query(self, dataset_id: str, request: Mapping[str, Any]) -> RasterResult:
        """Validate and execute a raster request.

        Args:
            dataset_id: Public dataset identifier.
            request: Request body as a mapping.

        Returns:
            The product with its processing metadata.
        """
        query = self.build_query(dataset_id, request)
        raster = query.dataset.raster
        assert raster is not None  # noqa: S101 - guaranteed by resolve_dataset
        logger.info(
            "Executing raster query dataset=%s variable=%s period=%s..%s selection=%s "
            "aggregation=%s format=%s",
            dataset_id,
            query.variable,
            query.start_date,
            query.end_date,
            query.selection.kind.value,
            query.aggregation.type.value,
            query.output_format.value,
        )
        layers = self._select_layers(query)
        steps: list[ProcessingStep] = [
            ProcessingStep(
                "source",
                "Layers resolved from the registered source without exposing storage paths.",
                {
                    "layers": len(layers),
                    "first": layers[0].label,
                    "last": layers[-1].label,
                    "temporal_resolution": raster.temporal_resolution.value,
                },
            )
        ]

        # Areas are clipped (and optionally warped); points are sampled.
        if query.selection.is_area:
            stack = self._reader.read_area(query.dataset, layers, query.selection, query.transform)
            data, grid, points = stack.data, stack.grid, []
            steps.append(
                ProcessingStep(
                    "clip",
                    "Source layers clipped to the geographic selection.",
                    {"selection": query.selection.kind.value},
                )
            )
            # The warp step is reported only when something was transformed.
            if not query.transform.is_identity:
                steps.append(
                    ProcessingStep(
                        "warp",
                        "Reprojected and/or resampled to the requested grid.",
                        {
                            "target_crs": grid.crs,
                            "resolution": list(grid.resolution),
                            "resampling": query.transform.resampling,
                        },
                    )
                )
        else:
            samples = self._reader.read_points(query.dataset, layers, query.selection)
            data, grid, points = samples.data, None, samples.points
            steps.append(
                ProcessingStep(
                    "sample",
                    "Values extracted at the requested coordinates.",
                    {"points": len(points)},
                )
            )

        # Unit conversion happens before aggregation so sums use the target unit.
        data, conversion_step = unit_conversion.convert_values(
            data, query.variable_definition, query.target_unit
        )
        if conversion_step is not None:
            steps.append(conversion_step)

        data, output_layers = self._aggregate(query, data, layers, steps)
        return RasterResult(
            dataset_id=query.dataset.id,
            variable=query.variable,
            unit=query.target_unit,
            applied_filters=dict(query.filters.applied),
            selection=query.selection,
            data=data,
            output_layers=output_layers,
            grid=grid,
            points=points,
            aggregation=query.aggregation.type,
            steps=steps,
            source_files=len({layer.uri for layer in layers}),
        )

    def _select_layers(self, query: RasterQuery) -> list[RasterLayer]:
        """Discover the layers of the query and keep the ones inside the period.

        Args:
            query: The validated query.

        Returns:
            Layers in chronological order.

        Raises:
            UnsupportedCombinationError: If the filter combination matches no file.
            PeriodOutsideCoverageError: If no layer overlaps the requested period.
            RequestTooLargeError: If more layers than allowed would be read.
        """
        available = self._reader.list_layers(query.dataset, query.filters)
        # A combination with no file at all is a client error naming the filters.
        if not available:
            raise UnsupportedCombinationError(
                "No data exists for the requested combination of filters "
                f"{dict(query.filters.applied)}.",
                parameter="filters",
            )
        layers = available
        # Period bounds keep only the overlapping layers.
        if query.start_date is not None and query.end_date is not None:
            layers = [
                layer
                for layer in available
                if temporal_windows.overlap_days(
                    query.start_date, query.end_date, layer.start, layer.end
                )
                > 0
            ]
        # Aggregation windows may extend beyond the period; include their layers too.
        for window in query.aggregation.windows:
            for layer in available:
                if layer not in layers and (
                    temporal_windows.overlap_days(window.start, window.end, layer.start, layer.end)
                    > 0
                ):
                    layers.append(layer)
        layers.sort(key=lambda item: item.start)
        # A period with no layer is reported with the available coverage.
        if not layers:
            raise PeriodOutsideCoverageError(
                "No layer falls inside the requested period; the dataset covers "
                f"{available[0].start.isoformat()} to {available[-1].end.isoformat()}.",
                parameter="start_date",
            )
        # Too many layers would exceed the service limits.
        if len(layers) > self._max_layers:
            raise RequestTooLargeError(
                f"The request selects {len(layers)} layers; at most {self._max_layers} can be "
                "read in one request. Narrow the period or aggregate.",
                parameter="start_date",
            )
        return layers

    def _aggregate(
        self,
        query: RasterQuery,
        data: np.ndarray,
        layers: list[RasterLayer],
        steps: list[ProcessingStep],
    ) -> tuple[np.ndarray, list[OutputLayer]]:
        """Apply the temporal aggregation, or describe the source layers as output.

        Args:
            query: The validated query.
            data: Values indexed by layer on the first axis.
            layers: Source layers.
            steps: Processing steps, extended with the aggregation when applied.

        Returns:
            The output array and the description of its slices.

        Raises:
            RequestTooLargeError: If the output exceeds the pixel budget.
        """
        aggregation = query.aggregation
        # Without aggregation every source layer is an output slice.
        if aggregation.type is AggregationType.NONE:
            self._check_budget(data)
            output_layers = [
                OutputLayer(layer.label, layer.start, layer.end, 1.0, [layer.label])
                for layer in layers
            ]
            return data, output_layers

        method = query.variable_definition.aggregation
        aggregated, output_layers = temporal_windows.aggregate_windows(
            data, layers, aggregation.windows, method
        )
        self._check_budget(aggregated)
        raster = query.dataset.raster
        parameters: dict[str, Any] = {
            "type": aggregation.type.value,
            "method": method.value,
            "windows": len(aggregation.windows),
            "source_temporal_resolution": raster.temporal_resolution.value if raster else None,
            **{
                key: value.isoformat() if isinstance(value, dt.date) else value
                for key, value in aggregation.parameters.items()
            },
        }
        steps.append(
            ProcessingStep(
                "temporal_aggregation",
                f"Layers aggregated with '{method.value}' over {len(aggregation.windows)} "
                "window(s); layers partially inside a window contribute in proportion to "
                "their overlapping days.",
                parameters,
            )
        )
        return aggregated, output_layers

    def _check_budget(self, data: np.ndarray) -> None:
        """Ensure the output stays within the configured pixel budget.

        Args:
            data: Output array.

        Raises:
            RequestTooLargeError: If the array has more cells than allowed.
        """
        # The budget bounds memory and response size.
        if data.size > self._max_pixels:
            raise RequestTooLargeError(
                f"The request would produce {data.size} cells; the limit is {self._max_pixels}. "
                "Reduce the area, the period or the resolution, or aggregate over time.",
                parameter="bbox",
            )
