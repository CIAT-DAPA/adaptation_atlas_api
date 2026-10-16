"""Schemas of the hazard exposure endpoint (HU-001)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from adaptation_atlas_api.api.schemas.common import ResponseMetadata
from adaptation_atlas_api.domain.models.results import TabularResult


class HazardExposureRecord(BaseModel):
    """One hazard exposure record.

    Fields follow the Adaptation Atlas hazard-risk-exposure tables. GAUL codes are
    ``null`` for sources that do not publish them.
    """

    model_config = ConfigDict(extra="allow")

    iso3: str | None = Field(default=None, description="ISO 3166-1 alpha-3 country code.")
    admin0_name: str | None = Field(default=None, description="Country name.")
    admin1_name: str | None = Field(default=None, description="First sub-national unit name.")
    admin2_name: str | None = Field(default=None, description="Second sub-national unit name.")
    gaul0_code: int | None = Field(default=None, description="GAUL code of the country.")
    gaul1_code: int | None = Field(default=None, description="GAUL code of the admin1 unit.")
    gaul2_code: int | None = Field(default=None, description="GAUL code of the admin2 unit.")
    source: str | None = Field(default=None, description="Climate data source.")
    scenario: str | None = Field(
        default=None, description="Emissions scenario (historic, ssp245...)."
    )
    model: str | None = Field(default=None, description="Climate model or ensemble statistic.")
    timeframe: str | None = Field(
        default=None, description="Period analysed (historic, 2021-2040...)."
    )
    season_type: str | None = Field(
        default=None, description="Calendar used (annual or jagermeyr)."
    )
    hazard: str | None = Field(default=None, description="Hazard or compound hazard grouping.")
    hazard_vars: str | None = Field(
        default=None, description="Hazard indices used in the calculation."
    )
    crop: str | None = Field(default=None, description="Crop or livestock commodity.")
    severity: str | None = Field(default=None, description="Hazard severity threshold.")
    exposure_var: str | None = Field(default=None, description="Exposure variable (e.g. vop).")
    exposure_unit: str | None = Field(default=None, description="Unit of the exposure variable.")
    value: float | None = Field(default=None, description="Hazard frequency x exposure value.")


class HazardExposureResponse(BaseModel):
    """JSON payload of the hazard exposure endpoint.

    Attributes:
        metadata: Pagination and filter metadata.
        data: Matching records (empty list when nothing matches).
    """

    metadata: ResponseMetadata
    data: list[HazardExposureRecord]

    @classmethod
    def from_result(cls, result: TabularResult) -> HazardExposureResponse:
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
        )
        return cls(metadata=metadata, data=[HazardExposureRecord(**row) for row in result.records])
