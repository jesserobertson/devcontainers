from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import typer
from logerr import Err, Ok, Result
from logerr.utilities import wrap_result
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from devtemplate import describe
from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status
from devtemplate.config import Settings, load_settings
from devtemplate.feature_graph import (
    FeatureSpec,
    describe_graph,
    load_cached_specs,
    ref_to_id,
)
from devtemplate.fuzzy import fuzzy_argument, resolve_or_confirm
from devtemplate.merge import merge_layer, merge_layer_keys
from devtemplate.schema import validate_devcontainer_config
from devtemplate.sidecar import load_sidecar, write_sidecar
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

__all__ = ["app"]

app = describe.Typer(
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
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    specs = load_cached_specs(settings)
    nodes = describe_graph(specs).unwrap_or({})

    names = list_cached_templates(settings)
    if not names and not json_output:
        console.print("No cached features. Run 'dvt sync' first.")
        raise typer.Exit(code=0)

    rows: list[dict[str, Any]] = []
    for name in names:
        match load_cached_template(settings, name):
            case Ok(template):
                rows.append(
                    {
                        "name": name,
                        "description": template.get("description", ""),
                        "image": template.get("image", ""),
                        "feature_ref": feature_ref(template),
                        "pulls_in": (
                            list(nodes[name].pulls_in) if name in nodes else []
                        ),
                    }
                )
            case Err(error):
                stderr_console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        print(json.dumps(rows))
        return

    table = Table("Name", "Description", "Pulls in", "Base Image")
    for row in rows:
        table.add_row(
            row["name"],
            row["description"],
            ", ".join(row["pulls_in"]) or "—",
            row["image"],
        )
    console.print(table)
    if not nodes:
        stderr_console.print("[dim]run 'dvt sync' for dependency info[/dim]")


def _dep_tree(fid: str, specs: Mapping[str, FeatureSpec]) -> Tree:
    """A Rich Tree rooted at `fid` whose children are its `dependsOn` subtree,
    recursively over `specs` (so it shows structure, not the flat closure). A
    node that declares `installsAfter` is annotated ` (after: x, y)` with the
    referenced ids."""

    def add(node_id: str, parent: Tree) -> None:
        spec = specs.get(node_id)
        suffix = ""
        if spec and spec.installs_after:
            labels = ", ".join(ref_to_id(r) for r in spec.installs_after)
            suffix = f" [dim](after: {labels})[/dim]"
        branch = parent.add(f"{node_id}{suffix}")
        for ref in spec.depends_on if spec else ():
            add(ref_to_id(ref), branch)

    root = Tree(fid)
    for ref in specs[fid].depends_on:
        add(ref_to_id(ref), root)
    return root


@app.command("show")
@fuzzy_argument(
    "name", candidates_fn=list_cached_templates, label="feature", console=console
)
def show_feature(
    name: str = typer.Argument(..., help="Cached feature name to show."),  # noqa: B008
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help='On failure, print {"ok": false, "error": ...} instead of '
        "human-readable text. On success --json adds a resolved_depends_on key "
        "(the transitive dependsOn closure) to the cached feature's raw "
        "devcontainer.json overlay; without --json the overlay is printed "
        "as-is followed by a dependency tree.",
    ),
) -> None:
    """Print a cached feature's devcontainer.json overlay and dependency tree."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    template = unwrap_or_exit(
        load_cached_template(settings, name), console, json_output=json_output
    )

    specs = load_cached_specs(settings)
    nodes = describe_graph(specs).unwrap_or({})

    if json_output:
        if name in nodes:
            print(
                json.dumps(
                    {**template, "resolved_depends_on": list(nodes[name].pulls_in)},
                    indent=2,
                )
            )
        else:
            print(json.dumps(template, indent=2))
        return

    print(json.dumps(template, indent=2))
    if name in specs:
        console.print(_dep_tree(name, specs))


# "description" is feature-registry metadata (used by 'dvt feature list'/'show'), not a
# devcontainer.json spec field - the schema is closed to unknown top-level keys, so it
# must never be merged into a consuming project's file.
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount", "description"}


@wrap_result
def add_one(
    name: str,
    settings: Settings,
    devcontainer_dir: Path,
    target: Path,
    *,
    json_output: bool = False,
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

    if not json_output:
        console.print(f"Added feature '{escape(name)}' to {escape(str(target))}.")


@app.command("add")
def add(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Cached feature name(s) to add, applied in order."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched feature name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Layer one or more features onto ./.devcontainer/devcontainer.json, in order."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    if not list_cached_templates(settings):

        def do_sync(_status: object) -> Result[list[str], Exception]:
            with httpx.Client() as client:
                return sync_templates(settings, client)

        sync_result = with_status(
            json_output, console, "Syncing features from GitHub...", do_sync
        )
        unwrap_or_exit(
            sync_result, console, prefix="Sync failed: ", json_output=json_output
        )

    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    resolved_names: list[str] = []
    for raw_name in names:
        resolved = unwrap_or_exit(
            resolve_or_confirm(
                raw_name,
                list_cached_templates(settings),
                label="feature",
                assume_yes=assume_yes,
                interactive=not json_output,
            ),
            console,
            json_output=json_output,
        )
        unwrap_or_exit(
            add_one(
                resolved, settings, devcontainer_dir, target, json_output=json_output
            ),
            console,
            json_output=json_output,
        )
        resolved_names.append(resolved)
    emit_success(json_output, {"added": resolved_names}, lambda: None)


@wrap_result
def remove_one(
    name: str, devcontainer_dir: Path, target: Path, *, json_output: bool = False
) -> None:
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

    if not json_output:
        console.print(f"Removed feature '{escape(name)}' from {escape(str(target))}.")


def _applied_feature_names(devcontainer_dir: Path) -> list[str]:
    """Candidates for remove's fuzzy resolution: the project's own currently-
    applied feature names (what remove actually operates on), not the full
    template cache - unlike add, remove must keep working for a feature whose
    template has since left the cache (e.g. pruned upstream). Returns [] on
    any read/parse failure (missing sidecar, corrupt JSON) so the caller can
    fall back to remove_one's own well-established "not tracked" error
    instead of a generic fuzzy-match error.
    """
    sidecar = load_sidecar(devcontainer_dir)
    if sidecar.is_err():
        return []
    applied = sidecar.unwrap().get("applied")
    if not isinstance(applied, list):
        return []
    return [
        name
        for entry in applied
        if isinstance(entry, dict) and isinstance(name := entry.get("name"), str)
    ]


@app.command("remove")
def remove(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Applied feature name(s) to remove, in order."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched feature name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Un-layer one or more features previously added with 'dvt feature add', in order."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    resolved_names: list[str] = []
    for raw_name in names:
        candidates = _applied_feature_names(devcontainer_dir)
        resolved = (
            raw_name
            if not candidates
            else unwrap_or_exit(
                resolve_or_confirm(
                    raw_name,
                    candidates,
                    label="feature",
                    assume_yes=assume_yes,
                    interactive=not json_output,
                ),
                console,
                json_output=json_output,
            )
        )
        unwrap_or_exit(
            remove_one(resolved, devcontainer_dir, target, json_output=json_output),
            console,
            json_output=json_output,
        )
        resolved_names.append(resolved)
    emit_success(json_output, {"removed": resolved_names}, lambda: None)
