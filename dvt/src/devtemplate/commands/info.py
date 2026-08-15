from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from logerr import Err, Ok  # noqa: F401
from rich.console import Console
from rich.markup import escape

from devtemplate.cli_support import emit_success, report_error
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_containers_by_folder
from devtemplate.runtime import get_client
from devtemplate.sidecar import load_sidecar
from devtemplate.store import load_cached_template

__all__ = ["info"]

console = Console()


def info(
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Show the current folder's devcontainer setup and any live workspace tied to it."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    if not target.exists():
        report_error(
            f"{target} not found. Run 'dvt init' first.",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1)

    try:
        config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        report_error(
            f"{target} is not strict JSON "
            "(comments/trailing commas are not supported).",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1) from exc

    settings_result = load_settings()

    def feature_description(name: str) -> str:
        # Untracked feature names are raw OCI refs straight out of
        # devcontainer.json's "features" map (e.g.
        # "ghcr.io/.../fastapi:latest"), not dvt template-cache names, so
        # this legitimately misses for them - not a bug, just nothing to
        # show. Same for a tracked name that's never been synced/cached.
        if settings_result.is_err():
            return ""
        template_result = load_cached_template(settings_result.unwrap(), name)
        if template_result.is_err():
            return ""
        return str(template_result.unwrap().get("description", ""))

    applied = load_sidecar(devcontainer_dir).map(lambda s: s["applied"]).unwrap_or([])
    if applied:
        feature_names = [entry["name"] for entry in applied]
        features_tracked = True
    else:
        feature_names = list(config.get("features", {}).keys())
        features_tracked = False

    features = [
        {"name": name, "description": feature_description(name)}
        for name in feature_names
    ]

    project: dict[str, Any] = {
        "name": config.get("name"),
        "path": str(Path.cwd()),
        "image": config.get("image"),
        "features": features,
        "features_tracked": features_tracked,
    }

    def print_human(footnote: str, style: str | None = None) -> None:
        console.print(
            f"Project:  {escape(str(config.get('name', '?')))}  ({escape(str(Path.cwd()))})"
        )
        console.print(f"Image:    {escape(str(config.get('image', '?')))}")
        if features:
            suffix = "" if features_tracked else " (untracked)"
            console.print(f"Features:{suffix}")
            for feature in features:
                name = escape(feature["name"])
                description = feature["description"]
                line = (
                    f"  - {name}: {escape(description)}"
                    if description
                    else f"  - {name}"
                )
                console.print(line)
        console.print()
        console.print(f"[{style}]{footnote}[/{style}]" if style else footnote)

    if settings_result.is_err():
        emit_success(
            json_output,
            {"project": project, "runtime_reachable": False, "workspace": None},
            lambda: print_human(escape(str(settings_result.unwrap_err())), "yellow"),
        )
        return
    settings = settings_result.unwrap()

    client_result = get_client(
        settings.runtime,
        podman_machine_auto_init=False,
        podman_machine_auto_start=False,
    )
    if client_result.is_err():
        emit_success(
            json_output,
            {"project": project, "runtime_reachable": False, "workspace": None},
            lambda: print_human(
                "No container runtime reachable - showing local config only.", "dim"
            ),
        )
        return
    handle = client_result.unwrap()

    try:
        containers = [
            c
            for c in find_workspace_containers_by_folder(handle.client, Path.cwd())
            if c.labels.get("dvt.workspace")
        ]
    except Exception as exc:
        # Bind the message now, not inside the lambda: `except ... as exc`
        # deletes `exc` when the block exits, and emit_success's non-json
        # branch calls this closure synchronously from within the block -
        # but ruff (correctly) can't prove that, and a future refactor that
        # deferred the call would turn this into a real NameError.
        footnote = f"Could not check for a live workspace: {escape(str(exc))}"
        emit_success(
            json_output,
            {"project": project, "runtime_reachable": True, "workspace": None},
            lambda: print_human(footnote, "yellow"),
        )
        return

    workspace: dict[str, Any] | None
    if not containers:
        workspace = {"status": "not_found"}
        footnote = "No workspace running for this project. Run 'dvt up' to start one."
    elif len(containers) == 1:
        container = containers[0]
        workspace_name = str(container.labels.get("dvt.workspace", "?"))
        workspace = {
            "status": container.status,
            "name": workspace_name,
            "container_name": str(container.name),
        }
        footnote = (
            f"Workspace: {escape(workspace_name)} - {escape(container.status)} "
            f"(container {escape(str(container.name))})"
        )
    else:
        matching_names = sorted(
            str(c.labels.get("dvt.workspace", "?")) for c in containers
        )
        workspace = {"status": "multiple", "names": matching_names}
        footnote = (
            f"Workspaces matching this folder: {escape(', '.join(matching_names))}"
        )

    emit_success(
        json_output,
        {"project": project, "runtime_reachable": True, "workspace": workspace},
        lambda: print_human(footnote),
    )
