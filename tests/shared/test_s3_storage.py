"""Tests of the S3 storage adapter that need no network access."""

from __future__ import annotations

import rasterio
from rasterio.session import AWSSession

from adaptation_atlas_api.shared.infrastructure.s3_storage import S3Storage


def test_gdal_options_build_a_rasterio_env_with_explicit_credentials() -> None:
    storage = S3Storage(
        bucket="digital-atlas",
        region="us-east-1",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret",
        session_token="token",
        endpoint_url=None,
    )
    options = storage.gdal_options()
    assert isinstance(options["session"], AWSSession)
    credentials = options["session"].credentials
    assert credentials["aws_access_key_id"] == "AKIAEXAMPLE"
    assert credentials["aws_session_token"] == "token"
    # rasterio must accept the arguments without complaining about AWS options.
    with rasterio.Env(**options):
        pass


def test_gdal_options_without_credentials_use_the_default_chain() -> None:
    storage = S3Storage(
        bucket="digital-atlas",
        region="eu-west-1",
        access_key_id=None,
        secret_access_key=None,
        session_token=None,
        endpoint_url="http://localhost:9000",
    )
    options = storage.gdal_options()
    assert isinstance(options["session"], AWSSession)
    with rasterio.Env(**options):
        pass
    assert storage.resolve_uri("a/b.tif") == "s3://digital-atlas/a/b.tif"
