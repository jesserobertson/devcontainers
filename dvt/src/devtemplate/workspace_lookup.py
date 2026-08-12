from __future__ import annotations

from pathlib import Path

from docker.client import DockerClient
from logerr.utilities import wrap_result

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


@wrap_result
def resolve_for_up(client: DockerClient, name: str | None, cwd: Path) -> str:
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
        return name
    names = _names_by_folder(client, cwd)
    if len(names) == 1:
        return names[0]
    if names:
        raise _multiple_matches_error("up", names)

    fallback_name = cwd.resolve().name
    existing = find_workspace_container(client, fallback_name)
    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        if existing_folder != str(cwd.resolve()):
            raise ValueError(
                f"A workspace named '{fallback_name}' already exists for a "
                f"different folder ({existing_folder or 'unknown'}). "
                "Pass an explicit name for this one."
            )
    return fallback_name


@wrap_result
def resolve_existing(
    client: DockerClient, name: str | None, cwd: Path, command: str
) -> str:
    """Same shape as resolve_for_up, for commands that only ever act on a
    workspace that already exists (ssh/stop/delete) - so no matches is also a
    refusal, not a directory-name fallback. `command` names the actual command
    that was run, so the refusal's suggested next step is accurate.
    """
    if name is not None:
        return name
    names = _names_by_folder(client, cwd)
    if len(names) == 1:
        return names[0]
    if not names:
        raise ValueError(
            "No workspace found for this folder. Specify a name, "
            "or run 'dvt up' to create one."
        )
    raise _multiple_matches_error(command, names)
