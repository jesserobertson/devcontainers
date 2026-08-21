from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer
from logerr import Err, Ok, Result
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status
from devtemplate.config import load_settings
from devtemplate.fuzzy import fuzzy_argument, resolve_or_confirm
from devtemplate.images import (
    create_image_file,
    delete_image_file,
    find_repo_root,
    list_cached_images,
    load_cached_image,
    sync_images,
    update_image_file,
)

__all__ = ["app"]

app = typer.Typer(help="List, sync, and manage the base images dvt knows about.")
console = Console()
stderr_console = Console(stderr=True)


@app.command("sync")
def sync(
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Refresh the cached image registry from GitHub."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    def do_sync(_status: object) -> Result[list[str], Exception]:
        with httpx.Client() as client:
            return sync_images(settings, client)

    result = with_status(json_output, console, "Syncing images from GitHub...", do_sync)
    names = unwrap_or_exit(
        result, console, prefix="Sync failed: ", json_output=json_output
    )
    emit_success(
        json_output,
        {"synced": names},
        lambda: console.print(f"Synced {len(names)} images: {', '.join(names)}"),
    )


@app.command("list")
def list_images(
    json_output: bool = typer.Option(  # noqa: B008
        False, "--json", help="Print machine-readable JSON instead of a table."
    ),
) -> None:
    """List every image dvt knows about, with its description and ref."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    names = list_cached_images(settings)
    if not names and not json_output:
        console.print("No cached images. Run 'dvt image sync' first.")
        raise typer.Exit(code=0)

    rows: list[dict[str, str]] = []
    for name in names:
        match load_cached_image(settings, name):
            case Ok(image):
                rows.append(
                    {
                        "name": name,
                        "description": image.get("description", ""),
                        "ref": image.get("ref", ""),
                    }
                )
            case Err(error):
                stderr_console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        print(json.dumps(rows))
        return

    table = Table("Name", "Description", "Ref")
    for row in rows:
        table.add_row(row["name"], row["description"], row["ref"])
    console.print(table)


@app.command("show")
@fuzzy_argument(
    "name", candidates_fn=list_cached_images, label="image", console=console
)
def show_image(
    name: str = typer.Argument(..., help="Cached image name to show."),  # noqa: B008
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text on failure.",
    ),
) -> None:
    """Print a cached image's raw metadata."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    image = unwrap_or_exit(
        load_cached_image(settings, name), console, json_output=json_output
    )
    print(json.dumps(image, indent=2))


@app.command("create")
def create(
    name: str = typer.Argument(..., help="New image name."),  # noqa: B008
    ref: str = typer.Option(  # noqa: B008
        ...,
        "--ref",
        help="Full OCI ref, e.g. ghcr.io/jesserobertson/base-ubuntu:latest.",
    ),
    description: str = typer.Option(  # noqa: B008
        ..., "--description", help="Short human-readable description."
    ),
    alias: list[str] = typer.Option(  # noqa: B008
        [],
        "--alias",
        help="Alternate name(s) this image can be resolved by (repeatable).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Write images/<name>.json in the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    path = unwrap_or_exit(
        create_image_file(
            repo_root, name, ref=ref, description=description, aliases=alias
        ),
        console,
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": name, "path": str(path)},
        lambda: console.print(
            f"Wrote {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )


@app.command("update")
def update(
    name: str = typer.Argument(  # noqa: B008
        ...,
        help="Image name to update, resolved against the current repo "
        "checkout's images/ directory (not the synced cache).",
    ),
    ref: str | None = typer.Option(None, "--ref", help="New OCI ref."),  # noqa: B008
    description: str | None = typer.Option(  # noqa: B008
        None, "--description", help="New description."
    ),
    alias: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--alias",
        help="New alias list (repeatable; replaces the existing list entirely).",
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
    """Edit fields on images/<name>.json in the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    images_dir = repo_root / "images"
    candidates = (
        sorted(p.stem for p in images_dir.glob("*.json")) if images_dir.exists() else []
    )
    resolved_name = (
        name
        if not candidates
        else unwrap_or_exit(
            resolve_or_confirm(
                name,
                candidates,
                label="image",
                assume_yes=assume_yes,
                interactive=not json_output,
            ),
            console,
            json_output=json_output,
        )
    )
    path = unwrap_or_exit(
        update_image_file(
            repo_root, resolved_name, ref=ref, description=description, aliases=alias
        ),
        console,
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": resolved_name, "path": str(path)},
        lambda: console.print(
            f"Updated {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )


@app.command("delete")
def delete(
    name: str = typer.Argument(  # noqa: B008
        ...,
        help="Image name to delete, resolved against the current repo "
        "checkout's images/ directory (not the synced cache).",
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
    """Remove images/<name>.json from the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    images_dir = repo_root / "images"
    candidates = (
        sorted(p.stem for p in images_dir.glob("*.json")) if images_dir.exists() else []
    )
    resolved_name = (
        name
        if not candidates
        else unwrap_or_exit(
            resolve_or_confirm(
                name,
                candidates,
                label="image",
                assume_yes=assume_yes,
                interactive=not json_output,
            ),
            console,
            json_output=json_output,
        )
    )
    path = unwrap_or_exit(
        delete_image_file(repo_root, resolved_name), console, json_output=json_output
    )
    emit_success(
        json_output,
        {"name": resolved_name, "path": str(path)},
        lambda: console.print(
            f"Removed {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )
