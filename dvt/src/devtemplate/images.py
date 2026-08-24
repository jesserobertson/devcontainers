from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, cast

import httpx
from logerr import Err, Ok, Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate.config import Settings
from devtemplate.fuzzy import resolve_or_confirm
from devtemplate.github import fetch_image_metadata, list_image_names

IMAGE_MANIFEST_KEY = "managed_images"
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

__all__ = [
    "find_repo_root",
    "list_cached_images",
    "load_cached_image",
    "read_image_manifest",
    "resolve_image_ref",
    "set_image_file",
    "sync_images",
    "unset_image_file",
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
        (settings.images_dir / f"{name}.json").write_text(
            json.dumps(metadata, indent=2)
        )

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
            f"No cached image named {name!r}. Run 'dvt sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))


@wrap_result
def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` (inclusive) looking for a `.git` directory.

    dvt image set/unset edit images/*.json directly in a real checkout of
    the source repo (see module docstring / design spec) - unlike
    sync/list/show, which work from a plain XDG cache with no checkout
    required at all.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError(
        f"No .git directory found in {start} or any parent - 'dvt image "
        "set/unset' must be run from inside a checkout of the devcontainers "
        "repo."
    )


@wrap_result
def set_image_file(
    repo_root: Path,
    name: str,
    *,
    ref: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> Path:
    """Create or update images/<name>.json in a repo checkout - an upsert.

    A brand-new name requires both `ref` and `description` (the full record
    a fresh file needs); an existing name only changes the fields passed -
    `aliases`, if passed, replaces the existing list entirely rather than
    appending to it.
    """
    validate_image_name(name).unwrap()
    images_dir = repo_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    path = images_dir / f"{name}.json"

    if not path.exists():
        if ref is None or description is None:
            raise ValueError(
                f"{path} doesn't exist yet - creating {name!r} needs both "
                "--ref and --description."
            )
        metadata: dict[str, Any] = {
            "name": name,
            "description": description,
            "ref": ref,
            "aliases": aliases or [],
        }
    else:
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
def unset_image_file(repo_root: Path, name: str) -> Path:
    validate_image_name(name).unwrap()
    path = repo_root / "images" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    path.unlink()
    return path


def resolve_image_ref(
    query: str,
    settings: Settings,
    *,
    assume_yes: bool = False,
    interactive: bool = True,
) -> Result[str, Exception]:
    """Resolve an --image argument to a full OCI ref.

    Exact match against a cached image's own ref, or its name/alias,
    resolves with no prompt. A close (but not exact) match to a name/alias
    goes through resolve_or_confirm - propagating a declined confirmation
    or a non-interactive suggestion as a real Err, since the user (or the
    fuzzy match itself) has something specific to say about it. An empty
    cache, or a query with no close match at all, passes the query through
    unchanged - this only ever helps resolve a name/alias dvt already
    knows about, it never blocks a literal ref that used to work.
    """
    names = list_cached_images(settings)
    if not names:
        return Ok(query)

    images: list[dict[str, Any]] = []
    for name in names:
        match load_cached_image(settings, name):
            case Ok(metadata) if isinstance(metadata, dict):
                images.append(metadata)
            case Ok(_) | Err(_):
                continue

    if any(query == image.get("ref") for image in images):
        return Ok(query)

    lookup: dict[str, str] = {}
    for image in images:
        name_value = image.get("name")
        ref_value = image.get("ref")
        if not isinstance(name_value, str) or not isinstance(ref_value, str):
            continue
        lookup[name_value] = ref_value
        aliases = image.get("aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str):
                    lookup[alias] = ref_value

    if query in lookup:
        return Ok(lookup[query])

    if not difflib.get_close_matches(query, sorted(lookup), n=1, cutoff=0.6):
        return Ok(query)

    resolved = resolve_or_confirm(
        query,
        sorted(lookup),
        label="image",
        assume_yes=assume_yes,
        interactive=interactive,
    )
    match resolved:
        case Ok(matched_key):
            return Ok(lookup[matched_key])
        case Err(error):
            return Err(error)
        case _:
            raise AssertionError("unreachable")
