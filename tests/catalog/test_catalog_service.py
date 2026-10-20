"""Unit tests for the catalog models and the YAML repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition
from adaptation_atlas_api.shared.domain.exceptions import CatalogConfigurationError
from adaptation_atlas_api.shared.infrastructure.yaml_catalog import YamlCatalogRepository


def test_real_catalog_loads_hazard_exposure(hazard_dataset: DatasetDefinition) -> None:
    assert hazard_dataset.id == "hazard_exposure"
    assert [f.name for f in hazard_dataset.required_filters] == ["country", "period"]
    assert hazard_dataset.template_placeholders() == [
        "source",
        "variable",
        "period",
        "model",
        "severity",
        "file",
    ]
    assert "value" in [field.name for field in hazard_dataset.response_fields]


def test_sidecar_metadata_is_merged(hazard_dataset: DatasetDefinition) -> None:
    source_metadata = hazard_dataset.metadata.get("source_metadata")
    assert isinstance(source_metadata, dict)
    assert source_metadata["unit"] == "nominal-usd-2021"


def test_invalid_definition_is_reported(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text(
        "id: broken\nname: Broken\nstorage:\n  path_template: 'x/{unknown}/{file}'\n",
        encoding="utf-8",
    )
    repository = YamlCatalogRepository(tmp_path)
    with pytest.raises(CatalogConfigurationError) as error:
        repository.load()
    assert "unknown" in error.value.message


def test_missing_directory_is_reported(tmp_path: Path) -> None:
    repository = YamlCatalogRepository(tmp_path / "nope")
    with pytest.raises(CatalogConfigurationError):
        repository.load()


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    content = "id: dup\nname: Dup\nstorage:\n  path_template: 'x/{file}'\n"
    (tmp_path / "a.yaml").write_text(content, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(content, encoding="utf-8")
    repository = YamlCatalogRepository(tmp_path)
    with pytest.raises(CatalogConfigurationError) as error:
        repository.load()
    assert "more than once" in error.value.message
