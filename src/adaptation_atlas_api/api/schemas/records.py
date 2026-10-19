"""Schemas of the generic records endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adaptation_atlas_api.api.schemas.common import ResponseMetadata
from adaptation_atlas_api.domain.models.results import TabularResult


class RecordsResponse(BaseModel):
    """JSON payload returned by ``GET /datasets/{dataset_id}/records``.

    The keys of every record are the fields declared by the dataset in the catalog (see
    ``GET /datasets/{dataset_id}``), or every source column when the dataset does not
    declare explicit fields.

    Attributes:
        metadata: Pagination and filter metadata.
        data: Matching records (empty list when nothing matches).
    """

    metadata: ResponseMetadata
    data: list[dict[str, Any]] = Field(
        ...,
        examples=[
            [
                {
                    "iso3": "KEN",
                    "admin0_name": "Kenya",
                    "admin1_name": "Nakuru",
                    "admin2_name": None,
                    "timeframe": "historic",
                    "crop": "maize",
                    "hazard": "dry+heat",
                    "severity": "moderate",
                    "exposure_unit": "intld15",
                    "value": 102546.0,
                }
            ]
        ],
    )

    @classmethod
    def from_result(cls, result: TabularResult) -> RecordsResponse:
        """Build the payload from a domain result.

        Args:
            result: Result returned by the service.

        Returns:
            The response model.
        """
        metadata = ResponseMetadata(
            dataset_id=result.dataset_id,
            filters=result.applied_filters,
            total_count=result.total_count,
            returned_count=result.returned_count,
            limit=result.limit,
            offset=result.offset,
            has_more=result.has_more,
            source_files=result.source_files,
            skipped_files=list(result.skipped_files),
            partial=result.is_partial,
        )
        return cls(metadata=metadata, data=result.records)
