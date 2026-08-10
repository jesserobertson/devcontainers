# dvt init + feature CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace dvt's `dvt project`/`dvt template` subgroups with a flat `dvt init` +
`dvt feature {list,show,sync,add,remove}` CRUD surface, add a `--json` output mode and
per-feature descriptions to `feature list`, and give the project real semantic versioning
(single source of truth, `dvt --version`, a maintained `CHANGELOG.md`).

**Architecture:** `dvt init` now writes only the boilerplate every template used to
duplicate (base image, `workspaceFolder`/`workspaceMount`, `remoteUser`,
`postCreateCommand`) and starts a new `.devcontainer/dvt-features.json` tracking sidecar.
`dvt feature add`/`remove` are the sole way features get layered on or off afterward, using
the existing field-typed `merge_layer` (unchanged) for `add` and a new `merge_layer_keys`
helper — the same per-field rules, replayed only over the touched keys — for a surgical
`remove` that never disturbs fields the removed feature didn't set, including manual edits.
`commands/project.py` and `commands/template.py` are deleted outright; a new
`commands/feature.py` and `commands/init.py` replace them. The data layer (`store.py`,
`merge.py`, `github.py`) keeps its existing "template" internal naming — only the CLI-facing
vocabulary changes.

**Tech Stack:** Python 3.12, Typer (CLI), Rich (console output), `logerr` (Result-typed
error handling), `jsonschema` (devcontainer.json schema validation), pytest.

## Global Constraints

- `dvt init` defaults `--image` to `ghcr.io/jesserobertson/base-ubuntu:latest`; the option's
  help text must show this default (`--help` visibly documents it, per project decision).
- No backward-compat aliases for `dvt project`/`dvt template` — deleted outright, this is a
  breaking change.
- `dvt feature add`/`remove` never leave `.devcontainer/devcontainer.json` (or the sidecar)
  partially written on any failure — build the new content fully in memory, validate, then
  write, exactly like today's `add-feature`.
- Every Typer command, argument, and option needs non-empty `help=` text — enforced by
  `tests/test_cli_help.py`, which must keep passing unmodified.
- `pyproject.toml`'s `version` moves `0.1.0` → `0.2.0` as part of this work (breaking CLI
  change).
- Run `pixi run test unit` and `pixi run quality check` from `dvt/` after each task; both
  must pass before moving on.

---

### Task 1: `merge_layer_keys` — scoped replay helper

**Files:**
- Modify: `src/devtemplate/merge.py` (add a function after `merge_layers`, currently ending
  at line 46)
- Test: `tests/test_merge.py` (append)

**Interfaces:**
- Produces: `merge_layer_keys(layers: list[dict[str, Any]], keys: set[str]) -> dict[str, Any]`
  — used by Task 6's `feature remove` to recompute only the fields a removed feature's
  overlay touched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merge.py`:

```python
from devtemplate.merge import merge_layer_keys, merge_layers


def test_merge_layer_keys_only_recomputes_requested_keys():
    layers = [
        {"image": "base", "remoteUser": "dev"},
        {"image": "override", "features": {"a": {}}},
    ]
    result = merge_layer_keys(layers, {"image"})
    assert result == {"image": "override"}


def test_merge_layer_keys_omits_key_no_layer_sets():
    layers = [{"remoteUser": "dev"}, {"features": {"a": {}}}]
    result = merge_layer_keys(layers, {"postStartCommand"})
    assert result == {}


def test_merge_layer_keys_respects_array_dedup_rule_scoped():
    layers = [{"mounts": ["m1"]}, {"mounts": ["m1", "m2"]}]
    result = merge_layer_keys(layers, {"mounts"})
    assert result == {"mounts": ["m1", "m2"]}


def test_merge_layer_keys_matches_merge_layers_for_full_key_set():
    layers = [
        {"image": "base", "features": {"a": {}}, "mounts": ["m1"]},
        {"image": "override", "features": {"b": {}}, "runArgs": ["--x"]},
    ]
    all_keys = {"image", "features", "mounts", "runArgs"}
    assert merge_layer_keys(layers, all_keys) == merge_layers(layers)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_merge.py -k merge_layer_keys -v`
Expected: FAIL with `ImportError: cannot import name 'merge_layer_keys'`

- [ ] **Step 3: Implement `merge_layer_keys`**

Add to `src/devtemplate/merge.py`, directly after `merge_layers`:

```python
def merge_layer_keys(layers: list[dict[str, Any]], keys: set[str]) -> dict[str, Any]:
    """Replay merge_layer's field-type rules across `layers` (lowest priority
    first), restricted to `keys`. Used by `feature remove` to recompute only
    the fields a removed feature's overlay touched, without re-merging (and
    so without risking a change to) anything else in the target file. A key
    in `keys` that no layer ever sets is simply absent from the result - the
    caller deletes it from the file rather than leaving an empty container.
    """
    result: dict[str, Any] = {}
    for layer in layers:
        filtered = {key: value for key, value in layer.items() if key in keys}
        result = merge_layer(result, filtered)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_merge.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/merge.py tests/test_merge.py
git commit -m "feat(dvt): add merge_layer_keys for scoped per-field replay"
```

---

### Task 2: Feature-tracking sidecar module

**Files:**
- Create: `src/devtemplate/sidecar.py`
- Test: `tests/test_sidecar.py`

**Interfaces:**
- Produces: `SIDECAR_FILENAME: str` (`"dvt-features.json"`),
  `sidecar_path(devcontainer_dir: Path) -> Path`,
  `load_sidecar(devcontainer_dir: Path) -> Result[dict[str, Any], Exception]` (returns
  `{"init": {...}, "applied": [{"name": str, "overlay": dict}, ...]}`, defaulting to
  `{"init": {}, "applied": []}` when the file doesn't exist),
  `write_sidecar(devcontainer_dir: Path, sidecar: dict[str, Any]) -> Result[None, Exception]`.
  Used by Task 3 (`init` writes the `init` block), Task 5 (`add` appends to `applied`), and
  Task 6 (`remove` reads/rewrites `applied`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sidecar.py`:

```python
from __future__ import annotations

import json

from devtemplate.sidecar import load_sidecar, sidecar_path, write_sidecar


def test_load_sidecar_defaults_when_missing(tmp_path):
    result = load_sidecar(tmp_path)
    assert result.is_ok()
    assert result.unwrap() == {"init": {}, "applied": []}


def test_write_then_load_round_trips(tmp_path):
    sidecar = {
        "init": {"image": "x"},
        "applied": [{"name": "fastapi", "overlay": {"image": "y"}}],
    }
    write_result = write_sidecar(tmp_path, sidecar)
    assert write_result.is_ok()

    loaded = load_sidecar(tmp_path)
    assert loaded.unwrap() == sidecar


def test_load_sidecar_reports_invalid_json(tmp_path):
    sidecar_path(tmp_path).write_text("not json")

    result = load_sidecar(tmp_path)
    assert result.is_err()


def test_write_sidecar_creates_parent_dir(tmp_path):
    target_dir = tmp_path / ".devcontainer"

    result = write_sidecar(target_dir, {"init": {}, "applied": []})

    assert result.is_ok()
    assert (target_dir / "dvt-features.json").exists()


def test_sidecar_path_is_named_dvt_features_json(tmp_path):
    assert sidecar_path(tmp_path) == tmp_path / "dvt-features.json"


def test_write_sidecar_produces_valid_json(tmp_path):
    write_sidecar(tmp_path, {"init": {"image": "x"}, "applied": []})

    data = json.loads(sidecar_path(tmp_path).read_text())
    assert data == {"init": {"image": "x"}, "applied": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_sidecar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.sidecar'`

- [ ] **Step 3: Implement the sidecar module**

Create `src/devtemplate/sidecar.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logerr import Err, Ok, Result

SIDECAR_FILENAME = "dvt-features.json"


def sidecar_path(devcontainer_dir: Path) -> Path:
    return devcontainer_dir / SIDECAR_FILENAME


def load_sidecar(devcontainer_dir: Path) -> Result[dict[str, Any], Exception]:
    """Load the feature-tracking sidecar, defaulting to an empty one (no init
    baseline, no applied features) if it doesn't exist yet - a project whose
    devcontainer.json wasn't scaffolded by 'dvt init' simply starts tracking
    from here.
    """
    path = sidecar_path(devcontainer_dir)
    if not path.exists():
        return Ok({"init": {}, "applied": []})
    try:
        data = json.loads(path.read_text())
        return Ok({"init": data.get("init", {}), "applied": data.get("applied", [])})
    except Exception as exc:
        return Err(exc)


def write_sidecar(
    devcontainer_dir: Path, sidecar: dict[str, Any]
) -> Result[None, Exception]:
    try:
        devcontainer_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path(devcontainer_dir).write_text(
            json.dumps(sidecar, indent=2) + "\n"
        )
        return Ok(None)
    except Exception as exc:
        return Err(exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_sidecar.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/sidecar.py tests/test_sidecar.py
git commit -m "feat(dvt): add the feature-tracking sidecar module"
```

---

### Task 3: `dvt init` command

**Files:**
- Create: `src/devtemplate/commands/init.py`
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `write_sidecar` from `devtemplate.sidecar` (Task 2).
- Produces: `init(path: Path, image: str) -> None` — a plain Typer-decoratable function
  (no `Typer()` app of its own), registered as a top-level command by Task 7's `cli.py`.
  `DEFAULT_IMAGE: str = "ghcr.io/jesserobertson/base-ubuntu:latest"` — reused by Task 10's
  docs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_init.py`:

```python
from __future__ import annotations

import json

import typer
from typer.testing import CliRunner

from devtemplate.commands.init import DEFAULT_IMAGE, init

app = typer.Typer()
app.command("init")(init)

runner = CliRunner()


def test_init_scaffolds_devcontainer_json_with_defaults(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-project"
    assert written["image"] == DEFAULT_IMAGE
    assert written["remoteUser"] == "dev"
    assert written["workspaceFolder"] == "/workspace"
    assert "features" not in written
    post_create = written["postCreateCommand"]
    assert "detached-environments = true" in post_create
    assert post_create.endswith("pixi install")
    assert post_create.index("detached-environments") < post_create.index(
        "pixi install"
    )


def test_init_help_text_mentions_default_image():
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert DEFAULT_IMAGE in result.output


def test_init_image_option_overrides_default(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, ["init", str(project_dir), "--image", "ghcr.io/jesserobertson/base-cuda:latest"]
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path):
    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text(
        '{"name": "existing"}'
    )

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 1
    assert (
        json.loads(
            (project_dir / ".devcontainer" / "devcontainer.json").read_text()
        )["name"]
        == "existing"
    )


def test_init_derives_name_from_target_directory(tmp_path):
    project_dir = tmp_path / "my-actual-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-actual-project"


def test_init_scaffolds_pixi_toml_when_absent(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    pixi_toml = project_dir / "pixi.toml"
    assert pixi_toml.exists()
    content = pixi_toml.read_text()
    assert 'name = "my-project"' in content
    assert '"conda-forge"' in content
    assert '"linux-64"' in content


def test_init_does_not_overwrite_existing_pixi_toml(tmp_path):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pixi.toml").write_text('[project]\nname = "already-here"\n')

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (
        project_dir / "pixi.toml"
    ).read_text() == '[project]\nname = "already-here"\n'


def test_init_does_not_write_pixi_toml_when_pyproject_toml_exists(tmp_path):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text('[tool.pixi.project]\nname = "x"\n')

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert not (project_dir / "pixi.toml").exists()


def test_init_writes_sidecar_with_init_block(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    sidecar = json.loads(
        (project_dir / ".devcontainer" / "dvt-features.json").read_text()
    )
    assert sidecar["applied"] == []
    assert sidecar["init"]["image"] == DEFAULT_IMAGE
    assert "pixi install" in sidecar["init"]["postCreateCommand"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_init.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.commands.init'`

- [ ] **Step 3: Implement `commands/init.py`**

Create `src/devtemplate/commands/init.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from devtemplate.sidecar import write_sidecar

console = Console()

DEFAULT_IMAGE = "ghcr.io/jesserobertson/base-ubuntu:latest"

_PIXI_DETACHED_ENVIRONMENTS_STEP = (
    "mkdir -p ~/.config/pixi && "
    "printf 'detached-environments = true\\n' >> ~/.config/pixi/config.toml"
)
_POST_CREATE_COMMAND = f"{_PIXI_DETACHED_ENVIRONMENTS_STEP} && pixi install"

_MINIMAL_PIXI_TOML = """\
[workspace]
name = "{name}"
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
python = ">=3.11"
"""


def _scaffold_pixi_toml(path: Path, name: str) -> None:
    """Write a minimal pixi.toml if the project doesn't already manage its
    own dependencies (via pixi.toml or a pyproject.toml with a [tool.pixi]
    table). Every feature's postCreateCommand runs 'pixi install', which
    fails outright with no manifest to install from - so a project scaffolded
    from nothing needs at least this much to make `dvt up` work end to end.
    """
    if (path / "pixi.toml").exists() or (path / "pyproject.toml").exists():
        return
    (path / "pixi.toml").write_text(_MINIMAL_PIXI_TOML.format(name=name))


def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),  # noqa: B008
    image: str = typer.Option(  # noqa: B008
        DEFAULT_IMAGE, help=f"Base image (default: {DEFAULT_IMAGE})."
    ),
) -> None:
    """Scaffold a new project's devcontainer.json with no features yet."""
    name = path.resolve().name

    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{escape(str(target))} already exists.[/red] "
            "Use 'dvt feature add' to layer onto it instead."
        )
        raise typer.Exit(code=1)

    config: dict[str, Any] = {
        "name": name,
        "image": image,
        "workspaceFolder": "/workspace",
        "workspaceMount": (
            "source=${localWorkspaceFolder},"
            "target=/workspace,type=bind,consistency=cached"
        ),
        "remoteUser": "dev",
        "postCreateCommand": _POST_CREATE_COMMAND,
    }

    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target}.")

    sidecar_result = write_sidecar(devcontainer_dir, {"init": config, "applied": []})
    if sidecar_result.is_err():
        console.print(
            "[yellow]Warning: failed to write the feature-tracking sidecar: "
            f"{escape(str(sidecar_result.unwrap_err()))}[/yellow]"
        )

    _scaffold_pixi_toml(path, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_init.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/init.py tests/test_init.py
git commit -m "feat(dvt): add dvt init, scaffolding boilerplate with no features"
```

---

### Task 4: `dvt feature list` / `show` / `sync`

**Files:**
- Create: `src/devtemplate/commands/feature.py`
- Modify: `src/devtemplate/store.py:133` (error message wording)
- Test: `tests/test_feature_command.py`

**Interfaces:**
- Consumes: `list_cached_templates`, `load_cached_template`, `sync_templates` from
  `devtemplate.store` (unchanged signatures).
- Produces: `app: typer.Typer` — the `feature` sub-app, registered by Task 7's `cli.py`.
  Tasks 5 and 6 add `add`/`remove` commands to this same `app`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_feature_command.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from devtemplate.commands.feature import app, console

runner = CliRunner()


def test_list_reports_no_features_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached features" in result.stdout


def test_list_shows_cached_feature_name_and_description(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "description": "FastAPI web APIs.",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "FastAPI web APIs." in result.stdout


def test_list_json_output_includes_all_fields(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "description": "FastAPI web APIs.",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "name": "fastapi",
            "description": "FastAPI web APIs.",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "feature_ref": "ghcr.io/jesserobertson/devcontainers/fastapi:latest",
        }
    ]


def test_list_json_output_defaults_missing_description_to_empty_string(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "cli").mkdir()
    (settings.templates_dir / "cli" / "devcontainer.json").write_text(
        json.dumps({"name": "cli", "image": "ghcr.io/x", "features": {}})
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows[0]["description"] == ""


def test_list_json_output_empty_cache_returns_empty_array(settings):
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_show_prints_cached_feature(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )

    result = runner.invoke(app, ["show", "fastapi"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_show_refuses_cleanly_on_unknown_feature(settings):
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code == 1
    assert "nonexistent" in result.stdout


def test_show_error_message_is_not_mangled_by_rich_markup(settings, monkeypatch):
    # Rich's color_system is fixed at Console() construction time (module import),
    # from whatever FORCE_COLOR/TTY state was live then - so in an environment that
    # sets FORCE_COLOR, styled segments get ANSI codes even when writing to
    # CliRunner's non-tty buffer. Force no_color directly so this test checks the
    # actual rendered text, not ANSI-interleaved bytes.
    monkeypatch.setattr(console, "no_color", True)

    result = runner.invoke(app, ["show", ".."])
    assert result.exit_code == 1
    assert "[a-z0-9][a-z0-9-]" in result.stdout


def test_sync_reports_synced_feature_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.feature.sync_templates",
        lambda settings_arg, client: Ok(["fastapi", "agent"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_feature_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.commands.feature'`

- [ ] **Step 3: Implement `commands/feature.py` (list/show/sync only)**

Create `src/devtemplate/commands/feature.py`:

```python
from __future__ import annotations

import json
from typing import Any

import httpx
import typer
from logerr import Err, Ok
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    sync_templates,
)

app = typer.Typer(
    help="Add, remove, and inspect the devcontainer features dvt knows about."
)
console = Console()


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
                console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        console.print_json(json.dumps(rows))
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
    console.print_json(json.dumps(template))


@app.command("sync")
def sync() -> None:
    """Refresh the cached feature registry from GitHub."""
    settings = unwrap_or_exit(load_settings(), console)

    with httpx.Client() as client:
        result = sync_templates(settings, client)
    names = unwrap_or_exit(result, console, prefix="Sync failed: ")
    console.print(f"Synced {len(names)} features: {', '.join(names)}")
```

Then fix the stale message in `src/devtemplate/store.py:133`, inside
`load_cached_template`:

```python
    try:
        return Ok(json.loads(path.read_text()))
    except Exception as exc:
        return Err(exc)
```

Change the line above it (currently `f"No cached template named {name!r}. Run 'dvt template sync' first."`)
to:

```python
            FileNotFoundError(
                f"No cached feature named {name!r}. Run 'dvt feature sync' first."
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_feature_command.py tests/test_store.py -v`
Expected: PASS (all tests; `test_store.py` doesn't assert the exact string, so it's
unaffected by the wording change)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py src/devtemplate/store.py tests/test_feature_command.py
git commit -m "feat(dvt): add dvt feature list/show/sync"
```

---

### Task 5: `dvt feature add`

**Files:**
- Modify: `src/devtemplate/commands/feature.py` (append a new command)
- Test: `tests/test_feature_command.py` (append)

**Interfaces:**
- Consumes: `merge_layer` from `devtemplate.merge` (unchanged), `load_sidecar`/
  `write_sidecar` from `devtemplate.sidecar` (Task 2).
- Produces: `dvt feature add <name>` on the `app` from Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_feature_command.py`:

```python
def test_add_merges_into_existing_devcontainer_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "my-project",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
                },
                "remoteUser": "dev",
            }
        )
    )

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "workspaceFolder": "/workspace",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                },
                "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
                "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
                "waitFor": "postStartCommand",
                "remoteUser": "dev",
            }
        )
    )

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output

    merged = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert merged["name"] == "my-project"
    assert "workspaceFolder" not in merged
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
        "ghcr.io/jesserobertson/devcontainers/agent:latest": {},
    }
    assert merged["runArgs"] == ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]
    assert merged["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert merged["waitFor"] == "postStartCommand"


def test_add_records_applied_feature_in_sidecar(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == [
        {
            "name": "agent",
            "overlay": {
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                }
            },
        }
    ]


def test_add_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 1


def test_add_refuses_on_invalid_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = '{\n  // a comment\n  "name": "my-project"\n}'
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["add", "agent"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_refuses_when_merge_result_is_schema_invalid(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = json.dumps(
        {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    )
    (devcontainer_dir / "devcontainer.json").write_text(original)

    template_dir = settings.templates_dir / "broken"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"remoteUser": 12345}))

    result = runner.invoke(app, ["add", "broken"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    from logerr import Ok

    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "agent"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": "agent",
                    "features": {
                        "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                    },
                }
            )
        )
        return Ok(["agent"])

    monkeypatch.setattr("devtemplate.commands.feature.sync_templates", fake_sync)

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_feature_command.py -k test_add -v`
Expected: FAIL with `AssertionError` (`No such command 'add'`, exit code 2)

- [ ] **Step 3: Implement `add`**

Append to `src/devtemplate/commands/feature.py` (add these imports to the existing import
block at the top: `from pathlib import Path`, `import jsonschema`,
`from devtemplate.merge import merge_layer`,
`from devtemplate.schema import validate_devcontainer_config`,
`from devtemplate.sidecar import load_sidecar, write_sidecar`):

```python
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount"}


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

    sidecar = unwrap_or_exit(load_sidecar(devcontainer_dir), console)
    sidecar["applied"].append({"name": name, "overlay": overlay})
    unwrap_or_exit(write_sidecar(devcontainer_dir, sidecar), console)

    console.print(f"Added feature '{name}' to {target}.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_feature_command.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "feat(dvt): add dvt feature add, tracking applied features in the sidecar"
```

---

### Task 6: `dvt feature remove`

**Files:**
- Modify: `src/devtemplate/commands/feature.py` (append a new command)
- Test: `tests/test_feature_command.py` (append)

**Interfaces:**
- Consumes: `merge_layer_keys` from `devtemplate.merge` (Task 1).
- Produces: `dvt feature remove <name>` on the `app` from Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_feature_command.py`:

```python
def test_remove_reverts_solo_feature_to_pre_add_state(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                },
                "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
                "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
                "waitFor": "postStartCommand",
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert "features" not in final
    assert "runArgs" not in final
    assert "postStartCommand" not in final
    assert "waitFor" not in final
    assert final["name"] == "my-project"
    assert final["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []


def test_remove_leaves_hand_edited_field_untouched(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                },
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "agent"])

    current = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    current["forwardPorts"] = [8000]
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(current))

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["forwardPorts"] == [8000]


def test_remove_earlier_feature_leaves_later_overlapping_field(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name, image in [
        ("fastapi", "ghcr.io/x/base-ubuntu:latest"),
        ("pytorch", "ghcr.io/x/base-cuda:latest"),
    ]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps({"name": template_name, "image": image})
        )

    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "my-project", "image": "ghcr.io/x/base-ubuntu:latest"})
    )
    runner.invoke(app, ["add", "fastapi"])
    runner.invoke(app, ["add", "pytorch"])

    remove_result = runner.invoke(app, ["remove", "fastapi"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["image"] == "ghcr.io/x/base-cuda:latest"


def test_remove_later_feature_restores_earlier_overlapping_field(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name, image in [
        ("fastapi", "ghcr.io/x/base-ubuntu:latest"),
        ("pytorch", "ghcr.io/x/base-cuda:latest"),
    ]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps({"name": template_name, "image": image})
        )

    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "my-project", "image": "ghcr.io/x/base-ubuntu:latest"})
    )
    runner.invoke(app, ["add", "fastapi"])
    runner.invoke(app, ["add", "pytorch"])

    remove_result = runner.invoke(app, ["remove", "pytorch"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["image"] == "ghcr.io/x/base-ubuntu:latest"


def test_remove_refuses_untracked_feature_name(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = json.dumps(
        {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    )
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["remove", "never-added"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_remove_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remove", "agent"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_feature_command.py -k test_remove -v`
Expected: FAIL with `AssertionError` (`No such command 'remove'`, exit code 2)

- [ ] **Step 3: Implement `remove`**

Append to `src/devtemplate/commands/feature.py` (add
`from devtemplate.merge import merge_layer_keys` to the existing `merge` import line, so it
reads `from devtemplate.merge import merge_layer, merge_layer_keys`):

```python
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
            "Only features added with 'dvt feature add' can be removed."
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_feature_command.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "feat(dvt): add dvt feature remove, a surgical per-field un-merge"
```

---

### Task 7: Wire up the CLI, remove the old subgroups

**Files:**
- Modify: `src/devtemplate/cli.py:1-30` (imports and app assembly)
- Modify: `src/devtemplate/workspace.py:39` (error message wording)
- Modify: `tests/test_cli_help.py` (append 2 tests)
- Delete: `src/devtemplate/commands/project.py`
- Delete: `src/devtemplate/commands/template.py`
- Delete: `tests/test_project_command.py`
- Delete: `tests/test_template_command.py`
- Create: `tests/test_cli_version.py`

**Interfaces:**
- Consumes: `feature.app` (Task 4-6), `init` from `devtemplate.commands.init` (Task 3),
  `devtemplate.__version__` (still `"0.1.0"` string literal at this point — Task 9 makes it
  a real single-sourced value; this task only wires up the callback that reads it).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_help.py`:

```python
def test_top_level_command_names_have_no_project_or_template_subgroups():
    names = set(root.commands.keys())
    assert "project" not in names
    assert "template" not in names
    assert "init" in names
    assert "feature" in names
```

Create `tests/test_cli_version.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_flag_works_without_settings_or_runtime(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("settings/runtime should not be touched by --version")

    monkeypatch.setattr("devtemplate.cli.load_settings", _boom)

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_cli_help.py tests/test_cli_version.py -v`
Expected: FAIL — `test_top_level_command_names_...` fails because `project`/`template` are
still registered; both `test_cli_version.py` tests fail with exit code 2 (`No such option:
--version`)

- [ ] **Step 3: Rewrite `cli.py`'s imports and app assembly**

Replace lines 1-30 of `src/devtemplate/cli.py` (everything from the top of the file through
`console = Console()`) with:

```python
from __future__ import annotations

from pathlib import Path

import logerr
import typer
from docker.client import DockerClient
from docker.models.containers import Container

# Err/Ok are re-exported here for tests to monkeypatch fakes via
# `cli_module.Ok(...)`/`cli_module.Err(...)` without importing logerr directly.
from logerr import Err, Ok  # noqa: F401
from loguru import logger
from rich.console import Console
from rich.markup import escape

from devtemplate import __version__
from devtemplate.cli_support import unwrap_or_exit
from devtemplate.commands import feature
from devtemplate.commands.init import init as init_command
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client
from devtemplate.ssh import exec_interactive, remove_ssh_config_entry, stdio_proxy
from devtemplate.workspace import up_workspace

app = typer.Typer(
    help="dvt: dev-style named devcontainer templates, built and run via Docker/Podman."
)
app.add_typer(feature.app, name="feature")
app.command("init")(init_command)
console = Console()


def _version_callback(value: bool) -> None:
    if not value:
        return
    console.print(f"dvt {__version__}")
    raise typer.Exit()


@app.callback()
def _root_callback(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show dvt's version and exit.",
    ),
) -> None:
    return
```

Leave everything from `@app.command()\ndef up(...)` onward (the rest of the file) exactly
as it is — `up`, `ssh`, `_find_or_exit`, `stop`, `delete`, and the bottom-of-file `main()`
entry point are unchanged.

- [ ] **Step 4: Fix the stale error message in `workspace.py`**

In `src/devtemplate/workspace.py`, inside `_load_config` (currently line 39), change:

```python
            FileNotFoundError(f"{config_file} not found. Run 'dvt project init' first.")
```

to:

```python
            FileNotFoundError(f"{config_file} not found. Run 'dvt init' first.")
```

- [ ] **Step 5: Delete the old command modules and their tests**

```bash
git rm src/devtemplate/commands/project.py src/devtemplate/commands/template.py
git rm tests/test_project_command.py tests/test_template_command.py
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pixi run pytest tests/ -m unit -v`
Expected: PASS — every test in the suite, including the new `test_cli_help.py`/
`test_cli_version.py` cases. (Task 8 still needs to update
`tests/integration/test_quickstart.py`, but that suite is `integration`-marked and doesn't
run here.)

- [ ] **Step 7: Commit**

```bash
git add src/devtemplate/cli.py src/devtemplate/workspace.py tests/test_cli_help.py tests/test_cli_version.py
git commit -m "$(cat <<'EOF'
feat(dvt): flatten the CLI into dvt init + dvt feature, add --version

Removes the dvt project/dvt template subgroups outright (no aliases) in
favor of dvt init and dvt feature {list,show,sync,add,remove}.
EOF
)"
```

---

### Task 8: Update the integration quickstart suite for the new CLI

**Files:**
- Modify: `tests/integration/test_quickstart.py`

**Interfaces:**
- Consumes: `app` from `devtemplate.cli` (now with `feature`/`init`, no `project`/
  `template`).

This suite is opt-in (`pixi run test integration`, real network + real Docker/Podman) and
isn't part of `pixi run test unit`/`all`/CI, so it can't have caught the CLI rename via the
unit suite — it needs its own pass. There's no "failing test" step here: the suite requires
a live runtime/network dvt can't assume is present in this environment, so verify by reading
the diff carefully against Task 4-7's actual command names, plus a dry run of the two tests
that don't need a container (`test_feature_sync_and_list`, `test_init_and_feature_add`) if
network access is available.

- [ ] **Step 1: Rewrite the command invocations**

Replace the whole content of `tests/integration/test_quickstart.py` with:

```python
"""Integration tests that run docs/content/quickstart.md's own command
sequence against the real jesserobertson/devcontainers GitHub repo and its
real GHCR-published Features - never a fake local template.

Opt-in only - run with `pixi run test integration`, never part of `pixi run test
all`, `pixi run pytest`, or CI. Requires network access and a reachable Docker
or Podman engine (skips cleanly, not a failure, if the runtime is unreachable;
a missing network connection surfaces as a real failure from `feature sync`,
since this suite's whole point is exercising the real thing).

Written after a live run-through of the quickstart surfaced four real bugs
that no unit test caught, because each one only manifests against a real base
image, a real published Feature, and a real container runtime:

- build.py's generated Dockerfile inherited the base image's own trailing
  `USER dev` for Feature install RUN steps, instead of the spec-mandated
  root - broke any Feature (like `cli`) that needs root during install.
- container.py never substituted devcontainer.json's `${localWorkspaceFolder}`
  variable in workspaceMount, so Docker/Podman misread the literal
  `${localWorkspaceFolder}` as an (invalid) named-volume name.
- `dvt init` never scaffolded a pixi.toml, so every feature's
  `postCreateCommand: "pixi install"` failed outright with nothing to install
  against - and even once scaffolded, installing straight into
  <project>/.pixi/envs (the workspaceMount bind mount) failed permission
  checks on at least Podman's WSL2 machine on Windows, fixed by turning on
  pixi's detached-environments config first.
- `dvt ssh` hardcoded `sh` for interactive sessions instead of the container
  user's real configured shell, so an image's own shell-startup hooks (this
  base image's `pixi shell-hook` in .bashrc/fish's conf.d) never fired.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client

runner = CliRunner()

pytestmark = pytest.mark.integration

runtime_unreachable = get_client("auto").is_err()


def test_feature_sync_and_list(settings) -> None:
    """Quickstart steps 1-2: `dvt feature sync` then `dvt feature list`
    against the real GitHub repo - no container runtime needed."""
    sync_result = runner.invoke(app, ["feature", "sync"])
    assert sync_result.exit_code == 0, sync_result.output
    assert "cli" in sync_result.output

    list_result = runner.invoke(app, ["feature", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert "cli" in list_result.output


def test_init_and_feature_add(settings, tmp_path, monkeypatch) -> None:
    """Quickstart steps 3-4: scaffold with `dvt init`, then layer two real
    features' requirements in via `dvt feature add`. Config-level only, no
    container build - the full build+run+ssh cycle for one feature is
    covered by test_quickstart_cli_feature_full_lifecycle below."""
    sync_result = runner.invoke(app, ["feature", "sync"])
    assert sync_result.exit_code == 0, sync_result.output

    project_dir = tmp_path / "my-cli-project"
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    devcontainer_path = project_dir / ".devcontainer" / "devcontainer.json"
    devcontainer = json.loads(devcontainer_path.read_text())
    assert devcontainer["name"] == "my-cli-project"
    # pixi.toml scaffolding + the workspace-table fix, see init.py.
    pixi_toml = (project_dir / "pixi.toml").read_text()
    assert "[workspace]" in pixi_toml

    monkeypatch.chdir(project_dir)
    add_cli_result = runner.invoke(app, ["feature", "add", "cli"])
    assert add_cli_result.exit_code == 0, add_cli_result.output

    add_devtools_result = runner.invoke(app, ["feature", "add", "py-devtools"])
    assert add_devtools_result.exit_code == 0, add_devtools_result.output

    merged = json.loads(devcontainer_path.read_text())
    assert merged["name"] == "my-cli-project"
    assert len(merged["features"]) == 2


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_quickstart_cli_feature_full_lifecycle(
    settings, tmp_path, monkeypatch
) -> None:
    """Quickstart steps 3-8 end to end against the real `cli` feature:

    - real GHCR Feature pull + image build (exercises build.py's USER root fix)
    - real workspaceMount with ${localWorkspaceFolder} (exercises
      container.py's variable-substitution fix)
    - a real postCreateCommand `pixi install` against dvt's own scaffolded
      pixi.toml, kept off the workspaceMount bind mount (exercises init.py's
      pixi.toml + detached-environments fixes)
    - a real `ssh` client through the actual ~/.ssh/config ProxyCommand entry
      `up` writes (proves the ssh_server.py bridge works against a real
      Feature-built image, not just the synthetic alpine one
      test_native_runtime_lifecycle.py uses)
    """
    sync_result = runner.invoke(app, ["feature", "sync"])
    assert sync_result.exit_code == 0, sync_result.output

    project_dir = tmp_path / "dvt-test-cli"
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init", str(project_dir)])
    assert init_result.exit_code == 0, init_result.output

    workspace_name = f"dvt-quickstart-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(project_dir)

    add_result = runner.invoke(app, ["feature", "add", "cli"])
    assert add_result.exit_code == 0, add_result.output

    try:
        up_result = runner.invoke(app, ["up", workspace_name])
        assert up_result.exit_code == 0, up_result.output

        # Direct exec_run, independent of the `dvt ssh` path exercised below:
        # proves postCreateCommand's `pixi install` actually produced a
        # working project environment.
        handle = get_client("auto").unwrap()
        container = find_workspace_container(handle.client, workspace_name)
        assert container is not None
        exit_code, output = container.exec_run(
            ["sh", "-c", "cd /workspace && pixi run python --version"]
        )
        output_text = output.decode(errors="replace")
        assert exit_code == 0, output_text
        assert "Python" in output_text

        ssh_binary = shutil.which("ssh")
        if ssh_binary is not None:
            ssh_result = subprocess.run(
                [
                    ssh_binary,
                    "-F",
                    str(Path.home() / ".ssh" / "config"),
                    workspace_name,
                    "echo hello-from-quickstart",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert ssh_result.returncode == 0, ssh_result.stderr
            assert "hello-from-quickstart" in ssh_result.stdout

        stop_result = runner.invoke(app, ["stop", workspace_name])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_name])
```

- [ ] **Step 2: Verify (network-only tests, if network access is available)**

Run: `pixi run pytest tests/integration/test_quickstart.py -k "not full_lifecycle" -v -m integration`
Expected: PASS for `test_feature_sync_and_list` and `test_init_and_feature_add` (both only
need network, not a container runtime). If there's no network access in this environment,
skip this verification step and rely on a careful read-through of the diff instead — note
that in the commit message.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_quickstart.py
git commit -m "test(dvt): update the quickstart integration suite for dvt init/feature"
```

---

### Task 9: Single source of truth for the version

**Files:**
- Modify: `src/devtemplate/__init__.py`
- Modify: `pyproject.toml:3`
- Test: `tests/test_version.py`

**Interfaces:**
- Produces: `devtemplate.__version__: str`, now derived from installed package metadata
  instead of a hardcoded literal — matches what Task 7's `cli.py` already imports.

- [ ] **Step 1: Bump the version in `pyproject.toml`**

In `pyproject.toml`, change line 3 from:

```toml
version = "0.1.0"
```

to:

```toml
version = "0.2.0"
```

- [ ] **Step 2: Make `__init__.py` read it from installed metadata**

Replace the entire contents of `src/devtemplate/__init__.py` (currently just
`__version__ = "0.1.0"`) with:

```python
from __future__ import annotations

from importlib.metadata import version

__version__ = version("devtemplate")
```

- [ ] **Step 3: Reinstall so the editable install's metadata picks up the bump**

Run: `pixi install`
Expected: completes without error (re-resolves the editable `devtemplate` package against
the updated `pyproject.toml`)

Run: `python -c "import devtemplate; print(devtemplate.__version__)"`
Expected: prints `0.2.0`

- [ ] **Step 4: Write the version-consistency test**

Create `tests/test_version.py`:

```python
from __future__ import annotations

import tomllib
from pathlib import Path

from devtemplate import __version__

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"


def test_version_matches_pyproject_toml():
    data = tomllib.loads(PYPROJECT_PATH.read_text())
    assert __version__ == data["project"]["version"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run pytest tests/test_version.py tests/test_cli_version.py -v`
Expected: PASS (all 3 tests — this also re-confirms Task 7's `--version` tests still pass
now that `__version__` is metadata-derived rather than a literal)

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/__init__.py pyproject.toml tests/test_version.py
git commit -m "feat(dvt): make pyproject.toml the single source of truth for the version"
```

---

### Task 10: `CHANGELOG.md`

**Files:**
- Create: `CHANGELOG.md` (at `dvt/`'s own root, alongside its `pyproject.toml`)

**Interfaces:** None — this is a standalone doc file.

- [ ] **Step 1: Write the changelog**

Create `dvt/CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 allowance that any release may
include breaking changes while bumping only the minor version — patch releases are fixes
only. Revisit switching breaking changes to a major bump once the project reaches `1.0.0`.

## [Unreleased]

## [0.2.0] - 2026-08-10

### Changed

- Flattened the CLI: `dvt project init`/`dvt project add-feature` and
  `dvt template {sync,list,show}` are replaced by `dvt init` and
  `dvt feature {list,show,sync,add,remove}`. No aliases kept — this is a breaking change.
- `dvt init` no longer scaffolds from a template; it writes a minimal boilerplate
  `devcontainer.json` (default base image `ghcr.io/jesserobertson/base-ubuntu:latest`,
  overridable with `--image`) with no features. Use `dvt feature add` to layer features on
  afterward.

### Added

- `dvt feature remove` — un-layers a previously added feature, restoring only the fields it
  touched (tracked via a new `.devcontainer/dvt-features.json` sidecar), leaving any other
  hand-edited fields untouched.
- `dvt feature list --json` — machine-readable feature registry listing; each entry includes
  a human-readable `description` (new field, backfilled onto every
  `templates/<name>/devcontainer.json`).
- `dvt --version`.

## [0.1.0] - 2026-07-23

Initial release: `dvt up`/`ssh`/`stop`/`delete` workspace lifecycle, `dvt project init`/
`add-feature`, `dvt template sync`/`list`/`show`.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(dvt): add CHANGELOG.md"
```

---

### Task 11: Rewrite docs for the new CLI, final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/content/commands.md`
- Modify: `docs/content/quickstart.md`
- Modify: `docs/content/installation.md:8`

**Interfaces:** None — docs only.

- [ ] **Step 1: Rewrite `README.md`'s Usage section**

In `README.md`, replace:

```
## Usage

    dvt template sync
    dvt template list
    dvt project init --template fastapi ./my-project
    dvt project add-feature agent      # run from inside a project with .devcontainer/devcontainer.json
    dvt up my-project
    dvt ssh my-project
```

with:

```
## Usage

    dvt feature sync
    dvt feature list
    dvt init ./my-project
    dvt feature add fastapi            # run from inside a project with .devcontainer/devcontainer.json
    dvt feature add agent
    dvt up my-project
    dvt ssh my-project
```

- [ ] **Step 2: Rewrite `docs/content/commands.md`**

Replace the file's contents from the top through the end of the `## `dvt project`` section
(everything before `## Workspace lifecycle`) with:

```markdown
# Command Reference

## `dvt init <path>`

Scaffolds `<path>/.devcontainer/devcontainer.json` with no features yet: a base image
(`--image`, default `ghcr.io/jesserobertson/base-ubuntu:latest`), `workspaceFolder`/
`workspaceMount`, `remoteUser: dev`, and a `postCreateCommand` that runs `pixi install`
(prefixed with a step that turns on pixi's `detached-environments` config — see
[Concepts](concepts.md)). The scaffolded file's `name` field is set to `<path>`'s own
directory name. Refuses (exit 1, nothing written) if
`<path>/.devcontainer/devcontainer.json` already exists. Also scaffolds a minimal
`pixi.toml` if the target directory doesn't already manage its own dependencies (via
`pixi.toml` or a `pyproject.toml` with a `[tool.pixi]` table) — every feature's
`postCreateCommand` runs `pixi install`, which needs one to install from.

## `dvt feature`

### `dvt feature sync`

Fetches every feature from `templates/` in the configured GitHub repository (default
`jesserobertson/devcontainers`, branch `main` — override with the `DVT_GITHUB_REPO` /
`DVT_GITHUB_BRANCH` environment variables) into the local cache. Prunes any previously-synced
feature that's been removed upstream; never touches a feature directory you've added by
hand.

### `dvt feature list`

Lists cached features with their description and base image. `--json` prints the same
data (plus each feature's OCI Feature ref) as a JSON array instead of a table, for
scripting.

### `dvt feature show <name>`

Prints a cached feature's devcontainer.json overlay.

### `dvt feature add <name>`

Merges a feature's overlay into `./.devcontainer/devcontainer.json` (always cwd-relative).
Auto-syncs first if the local cache is empty. See [Concepts](concepts.md) for the merge
semantics. Refuses to write (file left byte-for-byte unchanged) if:

- `.devcontainer/devcontainer.json` doesn't exist — run `dvt init` first
- it exists but isn't strict JSON (comments/trailing commas aren't supported)
- the feature name isn't cached and syncing doesn't produce it
- the merge result would fail validation against the official devcontainer.json schema

Also records the feature in `.devcontainer/dvt-features.json`, a tracking sidecar that
`dvt feature remove` uses to know what's safe to undo.

### `dvt feature remove <name>`

Un-layers a feature previously added with `dvt feature add`, restoring only the fields
that feature's overlay touched — anything else in the file, including manual edits made
since, is left untouched. Refuses (exit 1, nothing written) if `<name>` isn't tracked in
`.devcontainer/dvt-features.json` (never added via `dvt feature add`, or the file predates
it), or if the recomputed result would fail schema validation.
```

Then, near the end of the file, change the final line from:

```
These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `template`/`project` commands don't.
```

to:

```
These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `feature`/`init` commands don't.
```

Leave everything else in the file (the `## Workspace lifecycle` section onward) unchanged.

- [ ] **Step 3: Rewrite `docs/content/quickstart.md`**

Replace the entire file with:

```markdown
# Quickstart

This walkthrough scaffolds a new project, layers on the `fastapi` and `agent` features, and
starts it in a real container, built and run directly via Docker or Podman.

## 1. Sync features

Features are fetched from this repo's `templates/` directory on GitHub:

```bash
dvt feature sync
```

```
Synced 12 features: agent, cli, fastapi, huggingface, jax, marimo, mojo, ollama,
py-devtools, pytorch, rapids, transformers
```

## 2. See what's available

```bash
dvt feature list
```

## 3. Scaffold a project

```bash
dvt init ./my-api
```

This writes `./my-api/.devcontainer/devcontainer.json` with a default base image
(`ghcr.io/jesserobertson/base-ubuntu:latest` — override with `--image`) and `name` set to
the target directory's own name (`my-api`). No features are added yet.

## 4. Add features

```bash
cd my-api
dvt feature add fastapi
dvt feature add agent
```

Each `add` merges that feature's requirements (its own `features` entry, `runArgs`,
`postStartCommand`, etc.) into the existing `devcontainer.json` — see
[Concepts](concepts.md) for exactly how the merge works. If merging would produce an invalid
`devcontainer.json`, `add` refuses to write and leaves the file untouched.
`pytorch`/`rapids`/`jax`/`mojo`/`transformers` also override the base image to
`ghcr.io/jesserobertson/base-cuda:latest` when added, since they need a GPU.

## 5. Start the container

`up`'s `<name>` is the tag given to the workspace, not a path — run it from inside the
project directory:

```bash
dvt up my-api
```

`dvt` pulls each added Feature, builds a multi-stage image from it, runs the container,
and runs `postCreateCommand` (then `postStartCommand`, if a feature sets one).

## 6. Connect

```bash
dvt ssh my-api
```

## 7. Remove a feature

```bash
dvt feature remove agent
```

Restores the fields `agent` touched, leaving everything else in `devcontainer.json` —
including `fastapi`'s own contribution and any manual edits — untouched.

## 8. Stop or remove the workspace

```bash
dvt stop my-api
dvt delete my-api
```
```

- [ ] **Step 4: Fix `docs/content/installation.md`**

Change line 8 from:

```
- [Docker](https://www.docker.com/) or [Podman](https://podman.io/) — only needed for the
  `up`/`ssh`/`stop`/`delete` commands. `template`/`project` commands work without either.
```

to:

```
- [Docker](https://www.docker.com/) or [Podman](https://podman.io/) — only needed for the
  `up`/`ssh`/`stop`/`delete` commands. `feature`/`init` commands work without either.
```

- [ ] **Step 5: Full verification**

Run: `pixi run test unit`
Expected: PASS (every unit test in the suite)

Run: `pixi run quality check`
Expected: PASS (mypy, ruff lint, ruff format — all "Pass")

Run: `pixi run pytest tests/test_cli_help.py tests/test_cli_version.py tests/test_version.py tests/test_init.py tests/test_feature_command.py tests/test_sidecar.py tests/test_merge.py -v`
Expected: PASS (every test touched by this plan, run together once more as a final check)

- [ ] **Step 6: Commit**

```bash
git add README.md docs/content/commands.md docs/content/quickstart.md docs/content/installation.md
git commit -m "docs(dvt): rewrite README/commands/quickstart/installation for dvt init + dvt feature"
```
