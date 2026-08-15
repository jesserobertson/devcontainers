from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import typer
from logerr import Err, Ok
from logerr.utilities import wrap_result
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import Settings, load_settings
from devtemplate.features import clear_pulled_features
from devtemplate.merge import merge_layer, merge_layer_keys
from devtemplate.schema import validate_devcontainer_config
from devtemplate.sidecar import load_sidecar, write_sidecar
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

__all__ = ["app"]

app = typer.Typer(
    help="Add, remove, and inspect the devcontainer features dvt knows about."
)
console = Console()
stderr_console = Console(stderr=True)


def feature_ref(template: dict[str, Any]) -> str:
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
                        "feature_ref": feature_ref(template),
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
    """Refresh the cached feature registry from GitHub.

    Also clears the local cache of pulled devcontainer spec Feature artifacts
    (the OCI ref each template's "features" map points at, e.g.
    "ghcr.io/.../py-devtools:latest") - `dvt up` caches those forever once
    pulled once (see devtemplate.features.pull_feature), which is correct for
    an immutable version tag but means a moved `:latest` upstream would
    otherwise never be noticed on a machine that already pulled it. `sync` is
    the existing "go get whatever's current" entry point, so it clears both.
    """
    settings = unwrap_or_exit(load_settings(), console)

    clear_pulled_features(settings.features_dir)

    with httpx.Client() as client:
        result = sync_templates(settings, client)
    names = unwrap_or_exit(result, console, prefix="Sync failed: ")
    console.print(f"Synced {len(names)} features: {', '.join(names)}")


# "description" is feature-registry metadata (used by 'dvt feature list'/'show'), not a
# devcontainer.json spec field - the schema is closed to unknown top-level keys, so it
# must never be merged into a consuming project's file.
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount", "description"}


@wrap_result
def add_one(
    name: str, settings: Settings, devcontainer_dir: Path, target: Path
) -> None:
    """Layer one feature onto target's devcontainer.json. Prints its own
    success message and returns Ok(None) once devcontainer.json and the
    sidecar are both written. Returns Err (plain-text message, no Rich markup
    - the caller routes it through unwrap_or_exit, which escapes and colors
    it) on any failure: devcontainer.json missing/not strict JSON, the
    feature already applied, an uncached feature name, a schema-invalid
    merge result, or a sidecar write failure.
    """
    if not target.exists():
        raise FileNotFoundError(f"{target} not found. Run 'dvt init' first.")

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{target} is not strict JSON (comments/trailing commas are not "
            "supported). Add this feature's devcontainer.json snippet by "
            "hand instead."
        ) from exc

    # Load (and validate) the sidecar before writing anything below, so a
    # corrupt sidecar is caught up front rather than after devcontainer.json
    # has already been overwritten with the merge result.
    sidecar = load_sidecar(devcontainer_dir).unwrap()

    if any(entry["name"] == name for entry in sidecar["applied"]):
        raise ValueError(
            f"Feature {name!r} is already applied. Run 'dvt feature remove "
            f"{name}' first if you want to re-add it."
        )

    template = load_cached_template(settings, name).unwrap()

    overlay = {
        key: value for key, value in template.items() if key not in IDENTITY_FIELDS
    }
    merged = merge_layer(base_config, overlay)

    try:
        validate_devcontainer_config(merged)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            f"Adding {name!r} would produce an invalid devcontainer.json: {exc.message}"
        ) from exc

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
    write_sidecar(devcontainer_dir, sidecar).unwrap()

    console.print(f"Added feature '{escape(name)}' to {escape(str(target))}.")


@app.command("add")
def add(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Cached feature name(s) to add, applied in order."
    ),
) -> None:
    """Layer one or more features onto ./.devcontainer/devcontainer.json, in order."""
    settings = unwrap_or_exit(load_settings(), console)

    if not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_result = sync_templates(settings, client)
        unwrap_or_exit(sync_result, console, prefix="Sync failed: ")

    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    for name in names:
        unwrap_or_exit(add_one(name, settings, devcontainer_dir, target), console)


@wrap_result
def remove_one(name: str, devcontainer_dir: Path, target: Path) -> None:
    """Un-layer one feature previously added with 'dvt feature add'. Same
    Result/success-printing contract as add_one.
    """
    if not target.exists():
        raise FileNotFoundError(f"{target} not found. Run 'dvt init' first.")

    sidecar = load_sidecar(devcontainer_dir).unwrap()
    applied = sidecar["applied"]
    index = next(
        (i for i in range(len(applied) - 1, -1, -1) if applied[i]["name"] == name),
        None,
    )
    if index is None:
        raise ValueError(
            f"Feature {name!r} is not tracked for this project. dvt has no "
            "record of adding it - either "
            f"{devcontainer_dir / 'dvt-features.json'} doesn't exist yet, or "
            "this feature isn't in its list of applied features. Only "
            "features added with 'dvt feature add' can be removed this "
            "way.\n\n"
            f"To remove it by hand instead, edit {target} directly. To "
            f"rebuild tracking from scratch: back up {target}, delete it, "
            "run 'dvt init', then 'dvt feature add <name>' for each "
            "feature you want - this starts fresh tracking, but any "
            "manual customization won't carry over."
        )

    removed_entry = applied[index]
    remaining = applied[:index] + applied[index + 1 :]
    touched_keys = set(removed_entry["overlay"].keys())
    layers = [sidecar["init"], *(entry["overlay"] for entry in remaining)]
    recomputed = merge_layer_keys(layers, touched_keys)

    try:
        current = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{target} is not strict JSON (comments/trailing commas are not "
            "supported). Remove this feature's fields by hand instead."
        ) from exc

    updated = dict(current)
    for key in touched_keys:
        if key in recomputed:
            updated[key] = recomputed[key]
        else:
            updated.pop(key, None)

    try:
        validate_devcontainer_config(updated)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            f"Removing {name!r} would produce an invalid devcontainer.json: "
            f"{exc.message}"
        ) from exc

    target.write_text(json.dumps(updated, indent=2) + "\n")

    sidecar["applied"] = remaining
    write_sidecar(devcontainer_dir, sidecar).unwrap()

    console.print(f"Removed feature '{escape(name)}' from {escape(str(target))}.")


@app.command("remove")
def remove(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Applied feature name(s) to remove, in order."
    ),
) -> None:
    """Un-layer one or more features previously added with 'dvt feature add', in order."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    for name in names:
        unwrap_or_exit(remove_one(name, devcontainer_dir, target), console)
