from __future__ import annotations

import json
import re
from pathlib import Path
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
    "create_image_file",
    "delete_image_file",
    "find_repo_root",
    "list_cached_images",
    "load_cached_image",
    "read_image_manifest",
    "sync_images",
    "update_image_file",
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


@wrap_result
def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` (inclusive) looking for a `.git` directory.

    dvt image create/update/delete edit images/*.json directly in a real
    checkout of the source repo (see module docstring / design spec) -
    unlike sync/list/show, which work from a plain XDG cache with no
    checkout required at all.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError(
        f"No .git directory found in {start} or any parent - 'dvt image "
        "create/update/delete' must be run from inside a checkout of the "
        "devcontainers repo."
    )


@wrap_result
def create_image_file(
    repo_root: Path, name: str, *, ref: str, description: str, aliases: list[str]
) -> Path:
    validate_image_name(name).unwrap()
    images_dir = repo_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    path = images_dir / f"{name}.json"
    if path.exists():
        raise FileExistsError(
            f"{path} already exists. Use 'dvt image update {name}' to change it."
        )
    metadata = {"name": name, "description": description, "ref": ref, "aliases": aliases}
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return path


@wrap_result
def update_image_file(
    repo_root: Path,
    name: str,
    *,
    ref: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> Path:
    validate_image_name(name).unwrap()
    path = repo_root / "images" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Use 'dvt image create {name}' first."
        )
    metadata = cast(dict[str, Any], json.loads(path.read_text()))
    if ref is not None:
        metadata["ref"] = ref
    if description is not None:
        metadata["description"] = description
    if aliases is not None:
        metadata["aliases"] = aliases
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return path


@wrap_result
def delete_image_file(repo_root: Path, name: str) -> Path:
    validate_image_name(name).unwrap()
    path = repo_root / "images" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    path.unlink()
    return path
