"""Rendering of storage paths from a dataset template and validated filters.

Shared by every reader (tabular and raster): the path template of the dataset is
rendered once per combination of requested partition values and selected file pattern.
"""

from __future__ import annotations

import itertools

from adaptation_atlas_api.shared.domain.dataset import DatasetDefinition, extract_placeholders
from adaptation_atlas_api.shared.domain.filters import ValidatedFilters


def render_storage_paths(dataset: DatasetDefinition, filters: ValidatedFilters) -> list[str]:
    """Render the dataset path template for every requested partition/file combination.

    Each partition placeholder is replaced by each of its requested values (or ``*``
    when the partition is not filtered) and the ``{file}`` placeholder by the glob of
    each selected file pattern, producing one path per combination.

    Args:
        dataset: Dataset whose template is rendered.
        filters: Validated filters holding partition values and file pattern keys.

    Returns:
        Distinct paths (or globs) relative to the storage root, in a stable order.
    """
    storage = dataset.storage
    placeholders = [name for name in dataset.template_placeholders() if name != "file"]
    file_globs = [storage.file_patterns[key] for key in filters.file_pattern_keys]

    # Placeholders used inside file patterns also need their values.
    for file_glob in file_globs:
        for placeholder in extract_placeholders(file_glob):
            # Keep a stable order without duplicating template placeholders.
            if placeholder not in placeholders:
                placeholders.append(placeholder)

    # Every partition contributes its requested values, or a single wildcard.
    choices = [filters.partition_values.get(name, ["*"]) for name in placeholders]
    paths: list[str] = []

    # Build one path per file glob and per combination of partition values.
    for file_glob in file_globs:
        for combination in itertools.product(*choices):
            values = dict(zip(placeholders, combination, strict=True))
            rendered_glob = file_glob
            # Substitute the placeholders embedded in the file name first.
            for placeholder, value in values.items():
                rendered_glob = rendered_glob.replace("{" + placeholder + "}", value)
            path = storage.path_template.replace("{file}", rendered_glob)
            # Then substitute the folder placeholders of the template.
            for placeholder, value in values.items():
                path = path.replace("{" + placeholder + "}", value)
            # Identical paths can arise when a pattern ignores a placeholder.
            if path not in paths:
                paths.append(path)
    return paths


def static_prefix_of(path: str) -> str:
    """Return the part of a path or glob before its first wildcard.

    Args:
        path: Path possibly containing ``*`` or ``?``.

    Returns:
        The literal prefix usable for listing storage.
    """
    cut = len(path)
    # The prefix ends at the first wildcard character.
    for wildcard in ("*", "?", "["):
        position = path.find(wildcard)
        if position != -1:
            cut = min(cut, position)
    return path[:cut]
