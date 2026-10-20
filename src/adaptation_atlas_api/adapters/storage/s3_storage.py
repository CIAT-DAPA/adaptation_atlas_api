"""Storage adapter for datasets hosted in an AWS S3 bucket."""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from rasterio.session import AWSSession

from adaptation_atlas_api.ports.storage import Storage

logger = logging.getLogger(__name__)


class S3Storage(Storage):
    """Resolves ``s3://`` URIs and configures DuckDB's ``httpfs`` extension.

    Credentials are injected through DuckDB secrets so they never appear in SQL
    text or in log output.
    """

    def __init__(
        self,
        bucket: str,
        region: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        endpoint_url: str | None = None,
    ) -> None:
        """Create the adapter.

        Args:
            bucket: Bucket holding the datasets.
            region: AWS region of the bucket.
            access_key_id: AWS access key; ``None`` falls back to the default chain.
            secret_access_key: AWS secret key.
            session_token: Optional session token for temporary credentials.
            endpoint_url: Optional custom endpoint for S3 compatible services.
        """
        self._bucket = bucket
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token
        self._endpoint_url = endpoint_url
        self._client: Any = None

    @property
    def bucket(self) -> str:
        """Return the bucket name.

        Returns:
            The configured bucket name.
        """
        return self._bucket

    def resolve_uri(self, relative_path: str) -> str:
        """Build the ``s3://`` URI of a key relative to the bucket root.

        Args:
            relative_path: Key or glob relative to the bucket root.

        Returns:
            The fully qualified ``s3://bucket/key`` URI.
        """
        return f"s3://{self._bucket}/{relative_path.lstrip('/')}"

    def configure_connection(self, connection: Any) -> None:
        """Load ``httpfs`` and register the S3 credentials as a DuckDB secret.

        Args:
            connection: An open DuckDB connection.
        """
        connection.execute("INSTALL httpfs;")
        connection.execute("LOAD httpfs;")

        options = ["TYPE S3", f"REGION '{self._region}'"]

        # Explicit credentials are registered when provided; otherwise DuckDB uses the
        # standard AWS credential chain (environment, profile, instance role...).
        if self._access_key_id and self._secret_access_key:
            options.append(f"KEY_ID '{self._access_key_id}'")
            options.append(f"SECRET '{self._secret_access_key}'")
            # Temporary credentials also need the session token.
            if self._session_token:
                options.append(f"SESSION_TOKEN '{self._session_token}'")
        else:
            options.append("PROVIDER CREDENTIAL_CHAIN")

        # Custom endpoints (MinIO, LocalStack...) need the endpoint and path style.
        if self._endpoint_url:
            endpoint = self._endpoint_url.replace("https://", "").replace("http://", "")
            options.append(f"ENDPOINT '{endpoint}'")
            options.append("URL_STYLE 'path'")
            options.append(f"USE_SSL {str(self._endpoint_url.startswith('https')).lower()}")

        connection.execute(f"CREATE OR REPLACE SECRET atlas_s3 ({', '.join(options)});")
        logger.debug("DuckDB S3 secret configured for bucket '%s'", self._bucket)

    def gdal_options(self) -> dict[str, Any]:
        """Return the ``rasterio.Env`` arguments giving GDAL access to the bucket.

        rasterio does not accept AWS credentials as plain GDAL options; they travel in an
        :class:`rasterio.session.AWSSession`. Without explicit credentials the session
        falls back to the standard AWS credential chain (environment, profile, role).

        Returns:
            ``{"session": AWSSession(...)}`` for :class:`rasterio.Env`.
        """
        arguments: dict[str, Any] = {"region_name": self._region}

        # Explicit credentials are handed to the session; otherwise boto3 resolves them.
        if self._access_key_id and self._secret_access_key:
            arguments["aws_access_key_id"] = self._access_key_id
            arguments["aws_secret_access_key"] = self._secret_access_key
            # Temporary credentials also need the session token.
            if self._session_token:
                arguments["aws_session_token"] = self._session_token
        # Custom endpoints (MinIO, LocalStack...) are passed without the scheme.
        if self._endpoint_url:
            arguments["endpoint_url"] = self._endpoint_url.replace("https://", "").replace(
                "http://", ""
            )
        return {"session": AWSSession(**arguments)}

    def list_files(self, prefix: str, suffix: str = "") -> list[str]:
        """List keys under ``prefix`` using the paginated ``ListObjectsV2`` API.

        Args:
            prefix: Key prefix relative to the bucket root.
            suffix: Optional suffix the keys must end with.

        Returns:
            Matching keys relative to the bucket root.
        """
        paginator = self._get_client().get_paginator("list_objects_v2")
        keys: list[str] = []

        # Walk every page of results returned by S3.
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix.lstrip("/")):
            # Collect only the keys that match the requested suffix.
            for item in page.get("Contents", []):
                key = item["Key"]
                # Skip keys that do not end with the requested suffix.
                if suffix and not key.endswith(suffix):
                    continue
                keys.append(key)
        return keys

    def read_text(self, relative_path: str) -> str:
        """Download a small object and decode it as UTF-8.

        Args:
            relative_path: Key relative to the bucket root.

        Returns:
            The object content.
        """
        response = self._get_client().get_object(Bucket=self._bucket, Key=relative_path.lstrip("/"))
        return response["Body"].read().decode("utf-8")

    def _get_client(self) -> Any:
        """Create (lazily) and return the boto3 S3 client.

        Returns:
            The boto3 client.
        """
        # The client is created on first use so importing the adapter has no side effects.
        if self._client is None:
            self._client = boto3.client(
                "s3",
                region_name=self._region,
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                aws_session_token=self._session_token,
                endpoint_url=self._endpoint_url,
                config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
            )
        return self._client
