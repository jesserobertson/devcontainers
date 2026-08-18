from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate.config import Settings
from devtemplate.github import fetch_image_metadata, list_image_names

IMAGE_MANIFEST_KEY = "managed_images"
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

__all__ = [
    "list_cached_images",
    "load_cached_image",
    "read_image_manifest",
    "sync_images",
    "validate_image_name",
    "write_image_manifest",
]


def validate_image_name(name: str) -> Result[str, Exception]:
    error: Exception = ValueError(
        f"Invalid image name {name!r}: must match {IMAGE_NAME_PATTERN.pattern!r}"
    )
    return Result.from_predicate(
        name, lambda n: bool(IMAGE_NAME_PATTERN.fullmatch(n)), error
    )


@wrap_result
def read_image_manifest(settings: Settings) -> list[str]:
    if not settings.image_manifest_path.exists():
        return []
    data: dict[str, Any] = json.loads(settings.image_manifest_path.read_text())
    return cast(list[str], data.get(IMAGE_MANIFEST_KEY, []))


@wrap_result
def write_image_manifest(settings: Settings, managed_images: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.image_manifest_path.write_text(
        json.dumps({IMAGE_MANIFEST_KEY: sorted(managed_images)}, indent=2)
    )


@wrap_result
def sync_images(settings: Settings, client: httpx.Client) -> list[str]:
    """Fetch every images/<name>.json listed on GitHub into the local cache.

    Same contract as devtemplate.store.sync_templates: only ever writes to
    names GitHub currently lists (all validated first), prunes any name
    that was in the *previous* sync's manifest but is missing from this
    one, and never touches a file that was never in any manifest dvt
    itself wrote.
    """
    names = list_image_names(
        client, settings.github_repo, settings.github_branch
    ).unwrap()

    traverse_result(names, validate_image_name).unwrap()

    previous_names = read_image_manifest(settings).unwrap_or([])

    settings.images_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        metadata = fetch_image_metadata(
            client, settings.github_repo, settings.github_branch, name
        ).unwrap()
        (settings.images_dir / f"{name}.json").write_text(json.dumps(metadata, indent=2))

    removed = set(previous_names) - set(names)
    for stale_name in removed:
        if validate_image_name(stale_name).is_err():
            continue
        stale_file = settings.images_dir / f"{stale_name}.json"
        if stale_file.is_file():
            stale_file.unlink()

    write_image_manifest(settings, names).unwrap()
    return names


def list_cached_images(settings: Settings) -> list[str]:
    if not settings.images_dir.exists():
        return []
    return sorted(p.stem for p in settings.images_dir.glob("*.json"))


@wrap_result
def load_cached_image(settings: Settings, name: str) -> dict[str, Any]:
    validate_image_name(name).unwrap()
    path = settings.images_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached image named {name!r}. Run 'dvt image sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))
