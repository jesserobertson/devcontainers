from __future__ import annotations

import json

import httpx
import typer
from logerr import Err, Ok, Result
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status
from devtemplate.config import load_settings
from devtemplate.fuzzy import fuzzy_argument
from devtemplate.images import list_cached_images, load_cached_image, sync_images

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
@fuzzy_argument("name", candidates_fn=list_cached_images, label="image", console=console)
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
