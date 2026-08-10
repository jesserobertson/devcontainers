# Multi-name feature add/remove Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dvt feature add`/`remove` each accept one or more names, applied/removed in order,
stopping on the first failure (everything before it stays applied — a single name still behaves
byte-for-byte identically to today).

**Architecture:** Each command's per-name logic moves into a private `Result`-returning helper
(`_add_one`/`_remove_one`), matching the `logerr` convention already used throughout this
codebase. The public command becomes `names: list[str]` plus a loop that routes each result
through the existing `unwrap_or_exit` helper — which already gives "stop on first failure" for
free (returns and continues on `Ok`, prints and exits 1 on `Err`).

**Tech Stack:** Python 3.12, Typer, Rich, `logerr` (`Result`/`Ok`/`Err`), `jsonschema`, pytest.

## Global Constraints

- A single name must behave byte-for-byte identically to today's single-name commands — every
  existing test in `tests/test_feature_command.py` for `add`/`remove` must keep passing
  unmodified.
- Every `Err` constructed inside `_add_one`/`_remove_one` carries plain text, no Rich markup —
  `unwrap_or_exit` applies `escape()` and `[red]...[/red]` uniformly; do not hand-color inside
  the helpers.
- `dvt feature add`'s auto-sync-on-empty-cache check happens once, before the loop — not per
  name.
- Run `pixi run test unit` and `pixi run quality check` from `dvt/` before considering this
  done.

---

### Task 1: Multi-name `add`/`remove`

**Files:**
- Modify: `src/devtemplate/commands/feature.py` (imports at the top; replace the `add`/`remove`
  section, from `IDENTITY_FIELDS = ...` through the end of the file)
- Modify: `tests/test_feature_command.py` (append)

**Interfaces:**
- Produces: `_add_one(name: str, settings: Settings, devcontainer_dir: Path, target: Path) ->
  Result[None, Exception]`, `_remove_one(name: str, devcontainer_dir: Path, target: Path) ->
  Result[None, Exception]` — internal to `feature.py`, not consumed elsewhere.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_feature_command.py`:

```python
def test_add_multiple_names_applies_all_in_order(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )

    result = runner.invoke(app, ["add", "py-devtools", "marimo"])

    assert result.exit_code == 0, result.output
    assert "Added feature 'py-devtools'" in result.output
    assert "Added feature 'marimo'" in result.output

    merged = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {},
        "ghcr.io/jesserobertson/devcontainers/marimo:latest": {},
    }

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["py-devtools", "marimo"]


def test_add_stops_on_first_failure_leaving_earlier_successes_applied(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "py-devtools"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "py-devtools",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["add", "py-devtools", "typo-name", "marimo"])

    assert result.exit_code == 1
    assert "Added feature 'py-devtools'" in result.output
    assert "No cached feature named" in result.output
    assert "typo-name" in result.output
    assert "Added feature 'marimo'" not in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["py-devtools"]


def test_remove_multiple_names_removes_all_in_order(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools", "marimo"])

    result = runner.invoke(app, ["remove", "py-devtools", "marimo"])

    assert result.exit_code == 0, result.output
    assert "Removed feature 'py-devtools'" in result.output
    assert "Removed feature 'marimo'" in result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert "features" not in final

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []


def test_remove_stops_on_first_failure_leaving_earlier_removals_applied(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools", "marimo"])

    result = runner.invoke(app, ["remove", "py-devtools", "never-added", "marimo"])

    assert result.exit_code == 1
    assert "Removed feature 'py-devtools'" in result.output
    assert "is not tracked for this project" in result.output
    assert "Removed feature 'marimo'" not in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["marimo"]


def test_remove_same_name_twice_in_one_invocation_fails_on_the_second(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "py-devtools"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "py-devtools",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
                },
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools"])

    result = runner.invoke(app, ["remove", "py-devtools", "py-devtools"])

    assert result.exit_code == 1
    assert "Removed feature 'py-devtools'" in result.output
    assert "is not tracked for this project" in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_feature_command.py -k "multiple_names or stops_on_first_failure or same_name_twice" -v`
Expected: FAIL — `add`/`remove` currently only accept a single positional `name`, so invoking
with 2-3 arguments fails Click/Typer's argument parsing (exit code 2, "unexpected extra
argument").

Also run the full file to confirm nothing already broken: `pixi run pytest
tests/test_feature_command.py -v` — every existing test should still PASS at this point (they're
untouched).

- [ ] **Step 3: Update imports**

In `src/devtemplate/commands/feature.py`, change:

```python
from logerr import Err, Ok
```

to:

```python
from logerr import Err, Ok, Result
```

and change:

```python
from devtemplate.config import load_settings
```

to:

```python
from devtemplate.config import Settings, load_settings
```

- [ ] **Step 4: Replace the `add`/`remove` section**

Replace everything in `src/devtemplate/commands/feature.py` from `IDENTITY_FIELDS = ...` through
the end of the file with:

```python
# "description" is feature-registry metadata (used by 'dvt feature list'/'show'), not a
# devcontainer.json spec field - the schema is closed to unknown top-level keys, so it
# must never be merged into a consuming project's file.
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount", "description"}


def _add_one(
    name: str, settings: Settings, devcontainer_dir: Path, target: Path
) -> Result[None, Exception]:
    """Layer one feature onto target's devcontainer.json. Prints its own
    success message and returns Ok(None) once devcontainer.json and the
    sidecar are both written. Returns Err (plain-text message, no Rich markup
    - the caller routes it through unwrap_or_exit, which escapes and colors
    it) on any failure: devcontainer.json missing/not strict JSON, the
    feature already applied, an uncached feature name, a schema-invalid
    merge result, or a sidecar write failure.
    """
    if not target.exists():
        return Err(FileNotFoundError(f"{target} not found. Run 'dvt init' first."))

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError:
        return Err(
            ValueError(
                f"{target} is not strict JSON (comments/trailing commas are not "
                "supported). Add this feature's devcontainer.json snippet by "
                "hand instead."
            )
        )

    # Load (and validate) the sidecar before writing anything below, so a
    # corrupt sidecar is caught up front rather than after devcontainer.json
    # has already been overwritten with the merge result.
    sidecar_result = load_sidecar(devcontainer_dir)
    if sidecar_result.is_err():
        return Err(sidecar_result.unwrap_err())
    sidecar = sidecar_result.unwrap()

    if any(entry["name"] == name for entry in sidecar["applied"]):
        return Err(
            ValueError(
                f"Feature {name!r} is already applied. Run 'dvt feature remove "
                f"{name}' first if you want to re-add it."
            )
        )

    template_result = load_cached_template(settings, name)
    if template_result.is_err():
        return Err(template_result.unwrap_err())
    template = template_result.unwrap()

    overlay = {
        key: value for key, value in template.items() if key not in IDENTITY_FIELDS
    }
    merged = merge_layer(base_config, overlay)

    try:
        validate_devcontainer_config(merged)
    except jsonschema.ValidationError as exc:
        return Err(
            ValueError(
                f"Adding {name!r} would produce an invalid devcontainer.json: "
                f"{exc.message}"
            )
        )

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
    write_result = write_sidecar(devcontainer_dir, sidecar)
    if write_result.is_err():
        return Err(write_result.unwrap_err())

    console.print(f"Added feature '{escape(name)}' to {escape(str(target))}.")
    return Ok(None)


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
        unwrap_or_exit(_add_one(name, settings, devcontainer_dir, target), console)


def _remove_one(
    name: str, devcontainer_dir: Path, target: Path
) -> Result[None, Exception]:
    """Un-layer one feature previously added with 'dvt feature add'. Same
    Result/success-printing contract as _add_one.
    """
    if not target.exists():
        return Err(FileNotFoundError(f"{target} not found. Run 'dvt init' first."))

    sidecar_result = load_sidecar(devcontainer_dir)
    if sidecar_result.is_err():
        return Err(sidecar_result.unwrap_err())
    sidecar = sidecar_result.unwrap()
    applied = sidecar["applied"]
    index = next(
        (i for i in range(len(applied) - 1, -1, -1) if applied[i]["name"] == name),
        None,
    )
    if index is None:
        return Err(
            ValueError(
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
        )

    removed_entry = applied[index]
    remaining = applied[:index] + applied[index + 1 :]
    touched_keys = set(removed_entry["overlay"].keys())
    layers = [sidecar["init"], *(entry["overlay"] for entry in remaining)]
    recomputed = merge_layer_keys(layers, touched_keys)

    try:
        current = json.loads(target.read_text())
    except json.JSONDecodeError:
        return Err(
            ValueError(
                f"{target} is not strict JSON (comments/trailing commas are not "
                "supported). Remove this feature's fields by hand instead."
            )
        )

    updated = dict(current)
    for key in touched_keys:
        if key in recomputed:
            updated[key] = recomputed[key]
        else:
            updated.pop(key, None)

    try:
        validate_devcontainer_config(updated)
    except jsonschema.ValidationError as exc:
        return Err(
            ValueError(
                f"Removing {name!r} would produce an invalid devcontainer.json: "
                f"{exc.message}"
            )
        )

    target.write_text(json.dumps(updated, indent=2) + "\n")

    sidecar["applied"] = remaining
    write_result = write_sidecar(devcontainer_dir, sidecar)
    if write_result.is_err():
        return Err(write_result.unwrap_err())

    console.print(f"Removed feature '{escape(name)}' from {escape(str(target))}.")
    return Ok(None)


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
        unwrap_or_exit(_remove_one(name, devcontainer_dir, target), console)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run pytest tests/test_feature_command.py -v`
Expected: PASS — every test in the file, including every pre-existing single-name test
(unmodified) and the 5 new multi-name tests.

- [ ] **Step 6: Quality check**

Run: `pixi run quality check`
Expected: PASS (mypy, ruff check, ruff format — all "Pass"). `Result`/`Settings` are now used as
type annotations in `feature.py` — confirm mypy has no complaints about the new signatures.

- [ ] **Step 7: Full suite verification**

Run: `pixi run test unit`
Expected: PASS (every unit test in the suite). A known, pre-existing, unrelated flaky test
exists in `tests/test_ssh_server.py` (real-subprocess/thread/`asyncio.timeout`-based, not
touched by this change) — if it's the only failure, re-run `pixi run pytest
tests/test_ssh_server.py -v` in isolation to confirm it passes there, and treat it as the known
flake rather than a regression. Any other failure is a real problem.

- [ ] **Step 8: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "$(cat <<'EOF'
feat(dvt): accept multiple names in dvt feature add/remove

Both now apply/remove in the given order, stopping on the first
failure - names before it stay applied (each was already atomic), the
rest are never attempted. A single name is unchanged (a batch of one).
Per-name logic now returns a Result instead of printing/exiting
directly, routed through the same unwrap_or_exit every other command
already uses.
EOF
)"
```
