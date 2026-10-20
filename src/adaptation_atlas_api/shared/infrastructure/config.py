"""Application settings loaded from environment variables and the ``.env`` file.

All configuration values must live in the ``.env`` file (see ``.env.example``);
nothing environment-specific is hard-coded in the source tree.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed representation of every configuration variable used by the API.

    Attributes:
        app_name: Human readable application name shown in Swagger.
        app_version: Semantic version of the API.
        app_env: Deployment environment (development, staging or production).
        log_level: Logging verbosity.
        api_prefix: URL prefix under which every versioned router is mounted.
        cors_origins: Comma separated allowed CORS origins or ``*``.
        storage_backend: Storage adapter to use (``s3`` or ``local``).
        aws_access_key_id: AWS access key used by DuckDB ``httpfs``.
        aws_secret_access_key: AWS secret key used by DuckDB ``httpfs``.
        aws_session_token: Optional AWS session token for temporary credentials.
        aws_region: AWS region of the bucket.
        s3_bucket: Name of the bucket that holds the datasets.
        s3_endpoint_url: Optional custom endpoint for S3 compatible services.
        local_storage_root: Root directory when the local storage backend is used.
        catalog_path: Directory containing one YAML file per published dataset.
        partition_cache_ttl_seconds: Lifetime of the discovered partition index.
        duckdb_memory_limit: Memory limit passed to DuckDB.
        duckdb_threads: Number of threads DuckDB may use.
        query_timeout_seconds: Maximum seconds a single query may run.
        max_skipped_files: Maximum number of unreadable (corrupt or truncated) source
            files a single query may skip before failing with ``503``; ``0`` disables
            skipping so any unreadable file fails the query.
        max_rows_per_response: Hard cap of rows returned in one response.
        default_page_size: Page size applied when the client does not send ``limit``.
        raster_max_pixels: Maximum cells (layers x rows x columns) a raster request may
            read or produce.
        raster_max_layers: Maximum source layers (time steps) read in one raster request.
        raster_max_points: Maximum coordinates sampled in one raster request.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "Adaptation Atlas API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: str = "*"

    # Storage
    storage_backend: Literal["s3", "local"] = "s3"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    aws_region: str = "us-east-1"
    s3_bucket: str = "digital-atlas"
    s3_endpoint_url: str | None = None
    local_storage_root: Path | None = None

    # Catalog
    catalog_path: Path = Path("catalog")
    partition_cache_ttl_seconds: int = Field(default=3600, ge=0)

    # Query engine
    duckdb_memory_limit: str = "2GB"
    duckdb_threads: int = Field(default=4, ge=1)
    query_timeout_seconds: int = Field(default=60, ge=1)
    max_skipped_files: int = Field(default=5, ge=0)
    max_rows_per_response: int = Field(default=50_000, ge=1)
    default_page_size: int = Field(default=1_000, ge=1)

    # Raster engine
    raster_max_pixels: int = Field(default=50_000_000, ge=1)
    raster_max_layers: int = Field(default=600, ge=1)
    raster_max_points: int = Field(default=1_000, ge=1)

    @field_validator("aws_access_key_id", "aws_secret_access_key", "aws_session_token")
    @classmethod
    def _empty_string_to_none(cls, value: str | None) -> str | None:
        """Normalize blank credential strings to ``None``.

        Args:
            value: Raw value read from the environment.

        Returns:
            ``None`` when the value is empty or whitespace, otherwise the stripped value.
        """
        # An empty entry in .env (e.g. ``AWS_SESSION_TOKEN=``) must be treated as absent.
        if value is None or not value.strip():
            return None
        return value.strip()

    @field_validator("s3_endpoint_url", "local_storage_root", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Convert blank optional strings into ``None`` before type coercion.

        Args:
            value: Raw value read from the environment.

        Returns:
            ``None`` for blank strings, otherwise the original value.
        """
        # Blank optional values in .env must not be parsed as empty paths/URLs.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the configured CORS origins as a list.

        Returns:
            A list with every origin, or ``["*"]`` when all origins are allowed.
        """
        # A wildcard means every origin is accepted, so no splitting is needed.
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (once) and return the application settings.

    Returns:
        The cached :class:`Settings` instance.
    """
    return Settings()
