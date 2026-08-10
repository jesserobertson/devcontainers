from __future__ import annotations

from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate.container import (
    find_workspace_container,
    find_workspace_containers_by_folder,
)


def _names_by_folder(client: DockerClient, cwd: Path) -> list[str]:
    containers = find_workspace_containers_by_folder(client, cwd)
    return sorted(
        name
        for name in (container.labels.get("dvt.workspace") for container in containers)
        if name
    )


def _multiple_matches_error(command: str, names: list[str]) -> Exception:
    return ValueError(
        f"Multiple workspaces match this folder: {', '.join(names)}. "
        f"Run 'dvt {command} <name>' with one of these."
    )


def resolve_for_up(
    client: DockerClient, name: str | None, cwd: Path
) -> Result[str, Exception]:
    """Turn dvt up's optional name into a concrete one. An explicit name passes
    through unchanged. When omitted: exactly one workspace already tied to this
    folder (via its devcontainer.local_folder label) reuses that name; none yet
    falls back to the folder's own directory name, to create a fresh workspace
    (matching dvt init's own default-name derivation) - unless a workspace
    already exists under that name for a *different* folder, in which case
    this refuses rather than silently resuming someone else's workspace; more
    than one folder match refuses too, listing every candidate, since dvt
    won't guess which one you meant.
    """
    if name is not None:
        return Ok(name)
    try:
        names = _names_by_folder(client, cwd)
    except Exception as exc:
        return Err(exc)
    if len(names) == 1:
        return Ok(names[0])
    if names:
        return Err(_multiple_matches_error("up", names))

    fallback_name = cwd.resolve().name
    try:
        existing = find_workspace_container(client, fallback_name)
    except Exception as exc:
        return Err(exc)
    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        if existing_folder != str(cwd.resolve()):
            return Err(
                ValueError(
                    f"A workspace named '{fallback_name}' already exists for a "
                    f"different folder ({existing_folder or 'unknown'}). "
                    "Pass an explicit name for this one."
                )
            )
    return Ok(fallback_name)


def resolve_existing(
    client: DockerClient, name: str | None, cwd: Path, command: str
) -> Result[str, Exception]:
    """Same shape as resolve_for_up, for commands that only ever act on a
    workspace that already exists (ssh/stop/delete) - so no matches is also a
    refusal, not a directory-name fallback. `command` names the actual command
    that was run, so the refusal's suggested next step is accurate.
    """
    if name is not None:
        return Ok(name)
    try:
        names = _names_by_folder(client, cwd)
    except Exception as exc:
        return Err(exc)
    if len(names) == 1:
        return Ok(names[0])
    if not names:
        return Err(
            ValueError(
                "No workspace found for this folder. Specify a name, "
                "or run 'dvt up' to create one."
            )
        )
    return Err(_multiple_matches_error(command, names))
