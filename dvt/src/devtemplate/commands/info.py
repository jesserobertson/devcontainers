from __future__ import annotations

import json
from pathlib import Path

import typer
from logerr import Err, Ok  # noqa: F401
from rich.console import Console
from rich.markup import escape

from devtemplate.config import load_settings
from devtemplate.container import find_workspace_containers_by_folder
from devtemplate.runtime import get_client
from devtemplate.sidecar import load_sidecar

console = Console()


def info() -> None:
    """Show the current folder's devcontainer setup and any live workspace tied to it."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    if not target.exists():
        console.print(
            f"[red]{escape(str(target))} not found.[/red] Run 'dvt init' first."
        )
        raise typer.Exit(code=1)

    try:
        config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]{escape(str(target))} is not strict JSON "
            "(comments/trailing commas are not supported).[/red]"
        )
        raise typer.Exit(code=1) from exc

    console.print(
        f"Project:  {escape(str(config.get('name', '?')))}  ({escape(str(Path.cwd()))})"
    )
    console.print(f"Image:    {escape(str(config.get('image', '?')))}")

    sidecar_result = load_sidecar(devcontainer_dir)
    applied = sidecar_result.unwrap()["applied"] if sidecar_result.is_ok() else []
    if applied:
        names = ", ".join(entry["name"] for entry in applied)
        console.print(f"Features: {escape(names)}")
    else:
        feature_refs = list(config.get("features", {}).keys())
        if feature_refs:
            console.print(f"Features: {escape(', '.join(feature_refs))} (untracked)")

    console.print()

    settings_result = load_settings()
    if settings_result.is_err():
        console.print(f"[yellow]{escape(str(settings_result.unwrap_err()))}[/yellow]")
        return
    settings = settings_result.unwrap()

    client_result = get_client(
        settings.runtime,
        podman_machine_auto_init=False,
        podman_machine_auto_start=False,
    )
    if client_result.is_err():
        console.print(
            "[dim]No container runtime reachable - showing local config only.[/dim]"
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
        console.print(
            f"[yellow]Could not check for a live workspace: {escape(str(exc))}[/yellow]"
        )
        return
    if not containers:
        console.print(
            "No workspace running for this project. Run 'dvt up' to start one."
        )
    elif len(containers) == 1:
        container = containers[0]
        workspace_name = str(container.labels.get("dvt.workspace", "?"))
        console.print(
            f"Workspace: {escape(workspace_name)} - {escape(container.status)} "
            f"(container {escape(str(container.name))})"
        )
    else:
        matching_names = sorted(
            str(c.labels.get("dvt.workspace", "?")) for c in containers
        )
        console.print(
            f"Workspaces matching this folder: {escape(', '.join(matching_names))}"
        )
