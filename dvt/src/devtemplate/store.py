from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx

from devtemplate.config import Settings
from devtemplate.github import fetch_template, list_template_names

MANIFEST_KEY = "managed_templates"
TEMPLATE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _validate_template_name(name: str) -> None:
    if not TEMPLATE_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid template name {name!r}: must match {TEMPLATE_NAME_PATTERN.pattern!r}"
        )


def read_manifest(settings: Settings) -> list[str]:
    if not settings.manifest_path.exists():
        return []
    data = json.loads(settings.manifest_path.read_text())
    return cast(list[str], data.get(MANIFEST_KEY, []))


def write_manifest(settings: Settings, managed_templates: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps({MANIFEST_KEY: sorted(managed_templates)}, indent=2)
    )


def sync_templates(settings: Settings, client: httpx.Client) -> list[str]:
    """Fetch every template listed under templates/ on GitHub into the local cache.

    Only ever writes to the names GitHub currently lists, so any custom template
    directories a user has dropped in by hand under a different name are never touched.
    Every name is validated before use — `settings.github_repo` is user-overridable,
    so a malicious or compromised fork's directory listing is untrusted input.
    """
    names = list_template_names(client, settings.github_repo, settings.github_branch)
    for name in names:
        _validate_template_name(name)
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        template = fetch_template(client, settings.github_repo, settings.github_branch, name)
        template_dir = settings.templates_dir / name
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "devcontainer.json").write_text(json.dumps(template, indent=2))
    write_manifest(settings, names)
    return names


def list_cached_templates(settings: Settings) -> list[str]:
    if not settings.templates_dir.exists():
        return []
    return sorted(p.name for p in settings.templates_dir.iterdir() if p.is_dir())


def load_cached_template(settings: Settings, name: str) -> dict:
    _validate_template_name(name)
    path = settings.templates_dir / name / "devcontainer.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached template named {name!r}. Run 'dvt template sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))
