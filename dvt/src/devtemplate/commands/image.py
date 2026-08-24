from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from logerr import Err, Ok
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import emit_success, unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.fuzzy import fuzzy_argument, resolve_or_confirm, resolve_or_create
from devtemplate.images import (
    find_repo_root,
    list_cached_images,
    load_cached_image,
    set_image_file,
    unset_image_file,
)

__all__ = ["app"]

app = typer.Typer(help="List and manage the base images dvt knows about.")
console = Console()
stderr_console = Console(stderr=True)


def _fuzzy_repo_argument(
    *, allow_new: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for `dvt image set`/`unset`: fuzzy-resolve the wrapped
    command's `name` argument against the current directory's repo checkout
    (images/*.json), same "Did you mean X?"/--yes/--json contract as
    fuzzy_argument - but against the repo checkout rather than the
    settings-backed XDG cache, so it also injects the resolved `repo_root`
    into the wrapped function's kwargs alongside `name`.

    `allow_new=True` (`set`, an upsert) lets a name with no close match
    through unchanged - it may be a brand-new image, not a typo. `unset`
    (allow_new=False) errors on no match, since it must refer to something
    that already exists.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        original_sig = inspect.signature(func)
        cli_params = [
            p for p in original_sig.parameters.values() if p.name != "repo_root"
        ]
        yes_param = inspect.Parameter(
            "assume_yes",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                False,
                "--yes",
                "-y",
                help="Auto-accept a fuzzy-matched image name instead of prompting.",
            ),
            annotation=bool,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            assume_yes = kwargs.pop("assume_yes", False)
            json_output = kwargs.get("json_output", False)
            repo_root = unwrap_or_exit(
                find_repo_root(Path.cwd()), console, json_output=json_output
            )
            images_dir = repo_root / "images"
            candidates = (
                sorted(p.stem for p in images_dir.glob("*.json"))
                if images_dir.exists()
                else []
            )
            resolver = resolve_or_create if allow_new else resolve_or_confirm
            name = kwargs["name"]
            kwargs["name"] = (
                name
                if not candidates
                else unwrap_or_exit(
                    resolver(
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
            kwargs["repo_root"] = repo_root
            return func(*args, **kwargs)

        wrapper.__signature__ = original_sig.replace(  # type: ignore[attr-defined]
            parameters=[*cli_params, yes_param]
        )
        return wrapper

    return decorator


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
        console.print("No cached images. Run 'dvt sync' first.")
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


@app.command("set")
@_fuzzy_repo_argument(allow_new=True)
def set_image(
    name: str = typer.Argument(..., help="Image name to create or update."),  # noqa: B008
    ref: str | None = typer.Option(  # noqa: B008
        None,
        "--ref",
        help="OCI ref, e.g. ghcr.io/jesserobertson/base-ubuntu:latest "
        "(required for a new image).",
    ),
    description: str | None = typer.Option(  # noqa: B008
        None,
        "--description",
        help="Short human-readable description (required for a new image).",
    ),
    alias: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--alias",
        help="Alternate name(s) this image can be resolved by (repeatable; "
        "replaces the existing list entirely when updating).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
    *,
    repo_root: Path,
) -> None:
    """Create or update images/<name>.json in the current repo checkout.

    Doesn't publish to GitHub or affect the local cache - commit and push
    (or open a PR) yourself, then 'dvt sync' to pick it up locally.
    """
    path = unwrap_or_exit(
        set_image_file(
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


@app.command("unset")
@_fuzzy_repo_argument()
def unset_image(
    name: str = typer.Argument(  # noqa: B008
        ...,
        help="Image name to remove, resolved against the current repo "
        "checkout's images/ directory (not the synced cache).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
    *,
    repo_root: Path,
) -> None:
    """Remove images/<name>.json from the current repo checkout.

    Doesn't publish to GitHub or affect the local cache - commit and push
    (or open a PR) yourself, then 'dvt sync' to pick it up locally.
    """
    path = unwrap_or_exit(
        unset_image_file(repo_root, name), console, json_output=json_output
    )
    emit_success(
        json_output,
        {"name": name, "path": str(path)},
        lambda: console.print(
            f"Removed {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )
