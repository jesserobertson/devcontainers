from __future__ import annotations

import json
import re
import shutil
from typing import Any, cast

import httpx
from logerr import Err, Ok, Result

from devtemplate.config import Settings
from devtemplate.github import fetch_template, list_template_names

MANIFEST_KEY = "managed_templates"
TEMPLATE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_template_name(name: str) -> Result[str, ValueError]:
    if not TEMPLATE_NAME_PATTERN.fullmatch(name):
        return Err(
            ValueError(
                f"Invalid template name {name!r}: must match {TEMPLATE_NAME_PATTERN.pattern!r}"
            )
        )
    return Ok(name)


def read_manifest(settings: Settings) -> Result[list[str], Exception]:
    if not settings.manifest_path.exists():
        return Ok([])
    try:
        data = json.loads(settings.manifest_path.read_text())
        return Ok(data.get(MANIFEST_KEY, []))
    except Exception as exc:
        return Err(exc)


def write_manifest(
    settings: Settings, managed_templates: list[str]
) -> Result[None, Exception]:
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.manifest_path.write_text(
            json.dumps({MANIFEST_KEY: sorted(managed_templates)}, indent=2)
        )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def sync_templates(
    settings: Settings, client: httpx.Client
) -> Result[list[str], Exception]:
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
    names_result = list_template_names(
        client, settings.github_repo, settings.github_branch
    )
    if names_result.is_err():
        return names_result
    names = names_result.unwrap()

    for name in names:
        validation = _validate_template_name(name)
        if validation.is_err():
            # cast: logerr's Result[T, E] stub doesn't declare unwrap_err() on the
            # abstract base, only on the concrete Ok/Err subclasses, so mypy can't
            # see it here even though we've just confirmed .is_err(). Same cast()
            # idiom this codebase already used pre-retrofit for stub gaps.
            return Err(cast(Err[Any, Any], validation).unwrap_err())

    previous_result = read_manifest(settings)
    previous_names = previous_result.unwrap() if previous_result.is_ok() else []

    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        template_result = fetch_template(
            client, settings.github_repo, settings.github_branch, name
        )
        if template_result.is_err():
            return Err(cast(Err[Any, Any], template_result).unwrap_err())
        template_dir = settings.templates_dir / name
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(template_result.unwrap(), indent=2)
        )

    removed = set(previous_names) - set(names)
    for stale_name in removed:
        if _validate_template_name(stale_name).is_err():
            continue
        stale_dir = settings.templates_dir / stale_name
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)

    manifest_result = write_manifest(settings, names)
    if manifest_result.is_err():
        return Err(cast(Err[Any, Any], manifest_result).unwrap_err())
    return Ok(names)


def list_cached_templates(settings: Settings) -> list[str]:
    # No Result here: this never fails, it degrades to [] when templates_dir
    # doesn't exist yet — there's no failure mode to model.
    if not settings.templates_dir.exists():
        return []
    return sorted(p.name for p in settings.templates_dir.iterdir() if p.is_dir())


def load_cached_template(
    settings: Settings, name: str
) -> Result[dict[str, Any], Exception]:
    validation = _validate_template_name(name)
    if validation.is_err():
        return Err(cast(Err[Any, Any], validation).unwrap_err())
    path = settings.templates_dir / name / "devcontainer.json"
    if not path.exists():
        return Err(
            FileNotFoundError(
                f"No cached template named {name!r}. Run 'dvt template sync' first."
            )
        )
    try:
        return Ok(json.loads(path.read_text()))
    except Exception as exc:
        return Err(exc)
