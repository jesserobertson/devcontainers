from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import typer
from logerr import Err, Ok
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.merge import merge_layer, merge_layer_keys
from devtemplate.schema import validate_devcontainer_config
from devtemplate.sidecar import load_sidecar, write_sidecar
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

app = typer.Typer(
    help="Add, remove, and inspect the devcontainer features dvt knows about."
)
console = Console()
stderr_console = Console(stderr=True)


def _feature_ref(template: dict[str, Any]) -> str:
    features = template.get("features", {})
    return next(iter(features), "")


@app.command("list")
def list_features(
    json_output: bool = typer.Option(  # noqa: B008
        False, "--json", help="Print machine-readable JSON instead of a table."
    ),
) -> None:
    """List every feature dvt knows about, with its description."""
    settings = unwrap_or_exit(load_settings(), console)

    names = list_cached_templates(settings)
    if not names and not json_output:
        console.print("No cached features. Run 'dvt feature sync' first.")
        raise typer.Exit(code=0)

    rows: list[dict[str, str]] = []
    for name in names:
        match load_cached_template(settings, name):
            case Ok(template):
                rows.append(
                    {
                        "name": name,
                        "description": template.get("description", ""),
                        "image": template.get("image", ""),
                        "feature_ref": _feature_ref(template),
                    }
                )
            case Err(error):
                stderr_console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        print(json.dumps(rows))
        return

    table = Table("Name", "Description", "Base Image")
    for row in rows:
        table.add_row(row["name"], row["description"], row["image"])
    console.print(table)


@app.command("show")
def show_feature(
    name: str = typer.Argument(..., help="Cached feature name to show."),  # noqa: B008
) -> None:
    """Print a cached feature's devcontainer.json overlay."""
    settings = unwrap_or_exit(load_settings(), console)

    template = unwrap_or_exit(load_cached_template(settings, name), console)
    print(json.dumps(template, indent=2))


@app.command("sync")
def sync() -> None:
    """Refresh the cached feature registry from GitHub."""
    settings = unwrap_or_exit(load_settings(), console)

    with httpx.Client() as client:
        result = sync_templates(settings, client)
    names = unwrap_or_exit(result, console, prefix="Sync failed: ")
    console.print(f"Synced {len(names)} features: {', '.join(names)}")


# "description" is feature-registry metadata (used by 'dvt feature list'/'show'), not a
# devcontainer.json spec field - the schema is closed to unknown top-level keys, so it
# must never be merged into a consuming project's file.
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount", "description"}


@app.command("add")
def add(
    name: str = typer.Argument(..., help="Cached feature name to add."),  # noqa: B008
) -> None:
    """Layer a feature onto ./.devcontainer/devcontainer.json."""
    settings = unwrap_or_exit(load_settings(), console)

    if not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_result = sync_templates(settings, client)
        unwrap_or_exit(sync_result, console, prefix="Sync failed: ")

    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    if not target.exists():
        console.print(
            f"[red]{escape(str(target))} not found.[/red] Run 'dvt init' first."
        )
        raise typer.Exit(code=1)

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]{escape(str(target))} is not strict JSON "
            "(comments/trailing commas are not supported).[/red] "
            "Add this feature's devcontainer.json snippet by hand instead."
        )
        raise typer.Exit(code=1) from exc

    # Load (and validate) the sidecar before writing anything below, so a
    # corrupt sidecar is caught up front rather than after devcontainer.json
    # has already been overwritten with the merge result.
    sidecar = unwrap_or_exit(load_sidecar(devcontainer_dir), console)
    if any(entry["name"] == name for entry in sidecar["applied"]):
        console.print(
            f"[red]Feature '{escape(name)}' is already applied.[/red] "
            f"Run 'dvt feature remove {escape(name)}' first if you want to re-add it."
        )
        raise typer.Exit(code=1)

    template = unwrap_or_exit(load_cached_template(settings, name), console)

    overlay = {
        key: value for key, value in template.items() if key not in IDENTITY_FIELDS
    }
    merged = merge_layer(base_config, overlay)

    try:
        validate_devcontainer_config(merged)
    except jsonschema.ValidationError as exc:
        console.print(
            f"[red]Adding '{escape(name)}' would produce an invalid "
            f"devcontainer.json:[/red] {escape(exc.message)}"
        )
        raise typer.Exit(code=1) from exc

    target.write_text(json.dumps(merged, indent=2) + "\n")

    # "init" should capture the file's state immediately before the *first*
    # feature was ever layered on - whether that's dvt init's original
    # boilerplate or a hand-written file, and whether or not it's been
    # hand-edited since dvt started tracking it. Re-capturing it here
    # whenever "applied" is still empty (rather than only when no sidecar
    # file existed yet) covers both cases: a sidecar dvt init already wrote
    # still has an empty "applied" list at this point, so its "init" gets
    # refreshed to whatever the file actually looks like right now.
    if not sidecar["applied"]:
        sidecar["init"] = base_config
    sidecar["applied"].append({"name": name, "overlay": overlay})
    unwrap_or_exit(write_sidecar(devcontainer_dir, sidecar), console)

    console.print(f"Added feature '{name}' to {target}.")


@app.command("remove")
def remove(
    name: str = typer.Argument(..., help="Applied feature name to remove."),  # noqa: B008
) -> None:
    """Un-layer a feature previously added with 'dvt feature add'."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    if not target.exists():
        console.print(
            f"[red]{escape(str(target))} not found.[/red] Run 'dvt init' first."
        )
        raise typer.Exit(code=1)

    sidecar = unwrap_or_exit(load_sidecar(devcontainer_dir), console)
    applied = sidecar["applied"]
    index = next(
        (i for i in range(len(applied) - 1, -1, -1) if applied[i]["name"] == name),
        None,
    )
    if index is None:
        console.print(
            f"[red]Feature '{escape(name)}' is not tracked for this project.[/red] "
            "dvt has no record of adding it - either "
            f"{escape(str(devcontainer_dir / 'dvt-features.json'))} doesn't exist "
            "yet, or this feature isn't in its list of applied features. Only "
            "features added with 'dvt feature add' can be removed this way.\n\n"
            "To remove it by hand instead, edit "
            f"{escape(str(target))} directly. To rebuild tracking from scratch: "
            f"back up {escape(str(target))}, delete it, run 'dvt init', then "
            "'dvt feature add <name>' for each feature you want - this starts "
            "fresh tracking, but any manual customization won't carry over."
        )
        raise typer.Exit(code=1)

    removed_entry = applied[index]
    remaining = applied[:index] + applied[index + 1 :]
    touched_keys = set(removed_entry["overlay"].keys())
    layers = [sidecar["init"], *(entry["overlay"] for entry in remaining)]
    recomputed = merge_layer_keys(layers, touched_keys)

    try:
        current = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]{escape(str(target))} is not strict JSON "
            "(comments/trailing commas are not supported).[/red] "
            "Remove this feature's fields by hand instead."
        )
        raise typer.Exit(code=1) from exc

    updated = dict(current)
    for key in touched_keys:
        if key in recomputed:
            updated[key] = recomputed[key]
        else:
            updated.pop(key, None)

    try:
        validate_devcontainer_config(updated)
    except jsonschema.ValidationError as exc:
        console.print(
            f"[red]Removing '{escape(name)}' would produce an invalid "
            f"devcontainer.json:[/red] {escape(exc.message)}"
        )
        raise typer.Exit(code=1) from exc

    target.write_text(json.dumps(updated, indent=2) + "\n")

    sidecar["applied"] = remaining
    unwrap_or_exit(write_sidecar(devcontainer_dir, sidecar), console)

    console.print(f"Removed feature '{name}' from {target}.")
