from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer
from logerr import Result
from rich.console import Console
from rich.markup import escape

from devtemplate.cli_support import (
    emit_success,
    report_error,
    unwrap_or_exit,
    with_status,
)
from devtemplate.config import load_settings
from devtemplate.images import list_cached_images, resolve_image_ref, sync_images
from devtemplate.sidecar import write_sidecar

__all__ = ["init", "DEFAULT_IMAGE"]

console = Console()

DEFAULT_IMAGE = "ghcr.io/jesserobertson/base-ubuntu:latest"

PIXI_DETACHED_ENVIRONMENTS_STEP = (
    "mkdir -p ~/.config/pixi && "
    "printf 'detached-environments = true\\n' >> ~/.config/pixi/config.toml"
)
POST_CREATE_COMMAND = f"{PIXI_DETACHED_ENVIRONMENTS_STEP} && pixi install"

MINIMAL_PIXI_TOML = """\
[workspace]
name = "{name}"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.11"
"""


def scaffold_pixi_toml(path: Path, name: str) -> None:
    """Write a minimal pixi.toml if the project doesn't already manage its
    own dependencies (via pixi.toml or a pyproject.toml with a [tool.pixi]
    table). Every feature's postCreateCommand runs 'pixi install', which
    fails outright with no manifest to install from - so a project scaffolded
    from nothing needs at least this much to make `dvt up` work end to end.
    """
    if (path / "pixi.toml").exists() or (path / "pyproject.toml").exists():
        return
    (path / "pixi.toml").write_text(MINIMAL_PIXI_TOML.format(name=name))


def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),  # noqa: B008
    image: str = typer.Option(  # noqa: B008
        DEFAULT_IMAGE, help=f"Base image (default: {DEFAULT_IMAGE})."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched image name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Scaffold a new project's devcontainer.json with no features yet."""
    name = path.resolve().name

    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        report_error(
            f"{target} already exists. Use 'dvt feature add' to layer onto it instead.",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1)

    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    # "/" or ":" means `image` already looks like a literal OCI ref (every
    # real ref has one or both; a bare name/alias has neither) - skip the
    # sync entirely so a normal `dvt init` (default image, or an explicit
    # literal ref) never pays for a network round-trip it doesn't need.
    if "/" not in image and ":" not in image and not list_cached_images(settings):

        def do_sync(_status: object) -> Result[list[str], Exception]:
            with httpx.Client() as client:
                return sync_images(settings, client)

        # Best-effort: dvt init has never required network access and must keep
        # working offline with a literal --image ref (its historical behavior).
        # A sync failure here is silently discarded - resolve_image_ref below
        # falls through to its own empty-cache passthrough either way.
        with_status(json_output, console, "Syncing images from GitHub...", do_sync)

    resolved_image = unwrap_or_exit(
        resolve_image_ref(
            image, settings, assume_yes=assume_yes, interactive=not json_output
        ),
        console,
        json_output=json_output,
    )

    config: dict[str, Any] = {
        "name": name,
        "image": resolved_image,
        "workspaceFolder": "/workspace",
        "workspaceMount": (
            "source=${localWorkspaceFolder},"
            "target=/workspace,type=bind,consistency=cached"
        ),
        "remoteUser": "dev",
        "postCreateCommand": POST_CREATE_COMMAND,
    }

    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")

    sidecar_result = write_sidecar(devcontainer_dir, {"init": config, "applied": []})
    if sidecar_result.is_err() and not json_output:
        console.print(
            "[yellow]Warning: failed to write the feature-tracking sidecar: "
            f"{escape(str(sidecar_result.unwrap_err()))}[/yellow]"
        )

    scaffold_pixi_toml(path, name)

    emit_success(
        json_output,
        {"name": name, "path": str(target)},
        lambda: console.print(f"Scaffolded {target}."),
    )
