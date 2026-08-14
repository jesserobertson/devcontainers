from __future__ import annotations

import json
import re
import shutil
from typing import Any, cast

import httpx
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate.config import Settings
from devtemplate.github import fetch_template, list_template_names

MANIFEST_KEY = "managed_templates"
TEMPLATE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

__all__ = [
    "read_manifest",
    "write_manifest",
    "sync_templates",
    "list_cached_templates",
    "load_cached_template",
]


def validate_template_name(name: str) -> Result[str, Exception]:
    # error is typed as Exception (not the more specific ValueError) so
    # Result.from_predicate infers E=Exception here, matching every caller's
    # Result[..., Exception] - lets callers propagate .unwrap_err() straight
    # through without a cast, now that Result.unwrap_err() is declared on
    # the abstract base class.
    error: Exception = ValueError(
        f"Invalid template name {name!r}: must match {TEMPLATE_NAME_PATTERN.pattern!r}"
    )
    return Result.from_predicate(
        name, lambda n: bool(TEMPLATE_NAME_PATTERN.fullmatch(n)), error
    )


@wrap_result
def read_manifest(settings: Settings) -> list[str]:
    if not settings.manifest_path.exists():
        return []
    data: dict[str, Any] = json.loads(settings.manifest_path.read_text())
    return cast(list[str], data.get(MANIFEST_KEY, []))


@wrap_result
def write_manifest(settings: Settings, managed_templates: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps({MANIFEST_KEY: sorted(managed_templates)}, indent=2)
    )


@wrap_result
def sync_templates(settings: Settings, client: httpx.Client) -> list[str]:
    """Fetch every template listed under templates/ on GitHub into the local cache.

    Only ever writes to the names GitHub currently lists, so any custom template
    directories a user has dropped in by hand under a different name are never touched.
    Every name is validated before use — settings.github_repo is user-overridable, so a
    malicious or compromised fork's directory listing is untrusted input. All names are
    validated before templates_dir is created or anything is written, so a bad name
    anywhere in the listing aborts the whole sync with nothing written.

    Also prunes: any template that was in the *previous* sync's manifest but is missing
    from this sync's listing (removed or renamed upstream) has its local copy deleted.
    Only ever deletes names that were themselves previously written by dvt (i.e. present
    in the old manifest) — a hand-added custom template directory was never in any
    manifest dvt wrote, so it's never a pruning candidate.
    """
    names = list_template_names(
        client, settings.github_repo, settings.github_branch
    ).unwrap()

    traverse_result(names, validate_template_name).unwrap()

    previous_names = read_manifest(settings).unwrap_or([])

    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        template = fetch_template(
            client, settings.github_repo, settings.github_branch, name
        ).unwrap()
        template_dir = settings.templates_dir / name
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "devcontainer.json").write_text(json.dumps(template, indent=2))

    removed = set(previous_names) - set(names)
    for stale_name in removed:
        if validate_template_name(stale_name).is_err():
            continue
        stale_dir = settings.templates_dir / stale_name
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)

    write_manifest(settings, names).unwrap()
    return names


def list_cached_templates(settings: Settings) -> list[str]:
    # No Result here: this never fails, it degrades to [] when templates_dir
    # doesn't exist yet — there's no failure mode to model.
    if not settings.templates_dir.exists():
        return []
    return sorted(p.name for p in settings.templates_dir.iterdir() if p.is_dir())


@wrap_result
def load_cached_template(settings: Settings, name: str) -> dict[str, Any]:
    validate_template_name(name).unwrap()
    path = settings.templates_dir / name / "devcontainer.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached feature named {name!r}. Run 'dvt feature sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))
