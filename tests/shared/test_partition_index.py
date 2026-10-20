"""Adapter tests for the storage-backed partition index."""

from __future__ import annotations

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition
from adaptation_atlas_api.shared.infrastructure.local_storage import LocalStorage
from adaptation_atlas_api.shared.infrastructure.partition_index import StoragePartitionIndex


def test_combinations_are_discovered(
    local_storage: LocalStorage, hazard_dataset: DatasetDefinition
) -> None:
    index = StoragePartitionIndex(local_storage, ttl_seconds=0)
    combinations = index.available_combinations(hazard_dataset)
    assert len(combinations) == 3
    historic_moderate = next(
        c for c in combinations if c["model"] == "historic" and c["severity"] == "moderate"
    )
    assert set(historic_moderate["file_patterns"].split(",")) == {"interaction", "solo"}
    nex = next(c for c in combinations if c["source"] == "nex-gddp-cmip6")
    assert nex["file_patterns"] == "interaction"
    assert index.available_values(hazard_dataset, "severity") == ["moderate", "severe"]


def test_cache_is_used_until_refresh(
    local_storage: LocalStorage, hazard_dataset: DatasetDefinition
) -> None:
    index = StoragePartitionIndex(local_storage, ttl_seconds=3600)
    first = index.available_combinations(hazard_dataset)
    calls = {"count": 0}
    original = local_storage.list_files

    def counting_list_files(prefix: str, suffix: str = "") -> list[str]:
        calls["count"] += 1
        return original(prefix, suffix)

    local_storage.list_files = counting_list_files  # type: ignore[method-assign]
    try:
        assert index.available_combinations(hazard_dataset) == first
        assert calls["count"] == 0
        index.refresh(hazard_dataset)
        index.available_combinations(hazard_dataset)
        assert calls["count"] == 1
    finally:
        local_storage.list_files = original  # type: ignore[method-assign]
