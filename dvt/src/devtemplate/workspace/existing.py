from __future__ import annotations

from pathlib import Path
from typing import Any

from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Result
from logerr.utilities import wrap_result

from devtemplate.container import read_stored_config
from devtemplate.ssh import write_ssh_config_entry

__all__ = [
    "resume_existing",
    "config_drift_error",
    "folder_mismatch_error",
    "rebuild_teardown",
]


def refresh_ssh_config(name: str) -> Result[None, Exception]:
    return write_ssh_config_entry(name, Path.home() / ".ssh" / "config")


@wrap_result
def resume_existing(existing: Container, name: str) -> Container:
    """Handle the re-`up` case where a container already carries this
    workspace's label: start it if it isn't running, then (re)write its SSH
    config entry. Every fallible operation on `existing` - status access and
    start(), both of which can raise docker-py's APIError - is wrapped, so
    nothing escapes as a bare exception.
    """
    if existing.status != "running":
        existing.start()
    refresh_ssh_config(name).unwrap()
    return existing


def config_drift_error(
    existing: Container, config: dict[str, Any], name: str
) -> Exception:
    """Build the Err raised when an existing workspace's container doesn't
    match the current devcontainer.json. Distinguishes "config on disk
    differs from what was built" (lists the changed top-level keys) from
    "can't tell" (the container's own devcontainer.metadata label is
    unreadable) - both point at --rebuild, but the message says why."""
    stored_result = read_stored_config(existing)
    if stored_result.is_err():
        return ValueError(
            f"Workspace {name!r} already exists but dvt couldn't verify its "
            f"config ({stored_result.unwrap_err()}). Run 'dvt up --rebuild' "
            "to rebuild it."
        )
    stored = stored_result.unwrap()
    changed_keys = sorted(
        key
        for key in stored.keys() | config.keys()
        if stored.get(key) != config.get(key)
    )
    return ValueError(
        f"Workspace {name!r} already exists but its devcontainer.json has "
        f"changed since it was built ({', '.join(changed_keys)}). Run "
        "'dvt up --rebuild' to rebuild it, or revert devcontainer.json and "
        f"run 'dvt up' again. To use the existing container without going "
        f"through 'up' at all, run 'dvt ssh {name}'."
    )


def folder_mismatch_error(
    existing_folder: str | None, project_path: Path, name: str
) -> Exception:
    """Build the Err raised when --rebuild is invoked for a workspace that
    isn't *confirmed* to belong to the folder dvt is currently running from -
    either its devcontainer.local_folder label names a different folder, or
    the label is missing entirely (so there's nothing to confirm against at
    all). Rebuilding from the wrong vantage point, or an unconfirmed one,
    would tear down the real workspace and rebuild it with an unrelated
    project's config, so this refuses outright and leaves the container
    completely untouched."""
    if existing_folder is None:
        return ValueError(
            f"Workspace {name!r} exists but dvt can't confirm it was built "
            f"from '{project_path.resolve()}' (it has no "
            "devcontainer.local_folder label to check). Refusing to "
            "--rebuild it from here - run 'dvt up --rebuild' from the "
            "workspace's own folder instead."
        )
    return ValueError(
        f"Workspace {name!r} was built from {existing_folder!r}, but dvt is "
        f"running from '{project_path.resolve()}'. Run 'dvt up --rebuild' "
        "from the workspace's own folder instead."
    )


@wrap_result
def rebuild_teardown(client: DockerClient, existing: Container, image_tag: str) -> None:
    """Remove the existing container so the fresh-build path below can run as
    if no workspace existed yet. Only existing.remove() failing is fatal
    (surfaced as Err) - if the old container can't be removed, --rebuild
    can't safely proceed. Dropping the cached image tag afterward is
    best-effort and swallowed on failure: it's purely for `docker images`
    hygiene, since the upcoming build_image(nocache=True, pull=True) call
    overwrites the tag regardless and is what actually forces freshness, not
    this removal.
    """
    existing.remove(force=True)
    try:
        client.images.remove(image_tag, force=True)
    except Exception:
        pass
