# dvt (devtemplate) CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dvt`, a Typer/Rich/Pydantic CLI (package `devtemplate`) that scaffolds projects from named devcontainer templates fetched from this repo's `templates/` directory on GitHub, layers additional features onto an existing project's `devcontainer.json` via a field-typed merge, and passes lifecycle commands straight through to `devpod`.

**Architecture:** A self-contained `dvt/` subdirectory with its own `pyproject.toml` (pip/pipx-installable, pixi for local dev). Six focused modules — `config.py` (XDG settings), `models.py` (devcontainer.json shape), `merge.py` (pure field-typed merge, ported from `dev`'s `merge.rs`), `github.py` (network fetch), `store.py` (local cache + sync orchestration), `cli.py` + `commands/` (Typer surface) — built bottom-up so every later task only depends on interfaces earlier tasks already shipped and tested.

**Tech Stack:** Typer, Rich, Pydantic, pydantic-settings, `platformdirs`, `httpx`, pytest, `hypothesis`. Distribution: `pipx install ./dvt` (or a future git/PyPI ref); local dev loop via `pixi install` / `pixi run pytest` from `dvt/`.

## Global Constraints

- Package lives at `dvt/` in this repo (sibling to `templates/`, `features/`), fully self-contained: no imports reach into the repo root.
- `dvt/pyproject.toml` has both a standard `[project]`/`[build-system]` (hatchling) for pip/pipx, and a `[tool.pixi]` section (mirroring this repo's root `pyproject.toml`) for the dev loop only.
- Template store path: `platformdirs.user_data_dir("dvt")` — never a hardcoded `~/.dvt`.
- Default GitHub source: repo `jesserobertson/devcontainers`, branch `main`; overridable via `DVT_GITHUB_REPO` / `DVT_GITHUB_BRANCH` env vars (pydantic-settings, prefix `DVT_`).
- Template sync uses the GitHub Contents API (`api.github.com`) to list `templates/`, and `raw.githubusercontent.com` to fetch each `devcontainer.json` — no `git` dependency.
- Merge algorithm is field-typed, ported from `dev`'s `src/devcontainer/merge.rs`: scalar fields (`name`, `image`, `remoteUser`, `waitFor`, `shutdownAction`) — overlay overrides; `features` — union by key, overlay wins on collision; `mounts`/`forwardPorts` — concatenate with dedup; `runArgs` — concatenate **without** dedup (repeated flags are legitimate); `remoteEnv`/`containerEnv` — map merge, overlay wins on key collision; lifecycle fields (`postCreateCommand`, `postStartCommand`, `postAttachCommand`, `onCreateCommand`, `updateContentCommand`, `initializeCommand`) — union only if both sides are the named-command-object form, else overlay replaces outright; anything else unrecognized — overlay wins.
- `add-feature` additionally strips `name`, `workspaceFolder`, `workspaceMount` from the incoming template before merging — those are project-identity fields, not feature-declared fields, and every template in this repo sets its own `name` to its own feature name (e.g. `templates/agent/devcontainer.json` has `"name": "agent"`), so merging it unfiltered would silently rename the target project.
- JSON handling: plain `json` module only, strict parsing. If `add-feature`'s target file fails `json.loads` (comments/trailing commas), refuse to write and print the feature snippet instead.
- Pydantic models never use `Any` — every field has a concrete type (`bool | int | str` for feature options, unions of concrete types elsewhere) so `hypothesis`'s pydantic integration (`st.from_type(...)`) can generate valid instances without needing a registered `Any` strategy.
- All commands below assume the working directory is `dvt/` unless stated otherwise. Run tests with `pixi run pytest tests/<file> -v` from that directory.

---

### Task 1: Package scaffolding

**Files:**
- Create: `dvt/pyproject.toml`
- Create: `dvt/src/devtemplate/__init__.py`
- Create: `dvt/src/devtemplate/cli.py`
- Create: `dvt/README.md`
- Test: `dvt/tests/test_cli.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: an installable `devtemplate` package with a `dvt` console-script entry point (`devtemplate.cli:main`), and a Typer `app` object in `devtemplate.cli` that later tasks import and extend.

- [ ] **Step 1: Create the package manifest**

Create `dvt/pyproject.toml`:

```toml
[project]
name = "devtemplate"
version = "0.1.0"
description = "Layered, dev-style named devcontainer templates on top of DevPod"
requires-python = ">=3.11,<3.13"
dependencies = [
    "typer>=0.12",
    "rich>=13.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "platformdirs>=4.0",
    "httpx>=0.27",
]

[project.scripts]
dvt = "devtemplate.cli:main"

[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "hypothesis>=6.100",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/devtemplate"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.pixi.workspace]
channels = ["conda-forge"]
platforms = ["win-64", "linux-64"]

[tool.pixi.pypi-dependencies]
devtemplate = { path = ".", editable = true }

[tool.pixi.dependencies]
python = ">=3.11,<3.13"
pytest = ">=8.0"
hypothesis = ">=6.100"
```

- [ ] **Step 2: Create the empty package**

Create `dvt/src/devtemplate/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Create a minimal README**

Create `dvt/README.md`:

```markdown
# dvt (devtemplate)

Dev-style named devcontainer templates on top of [DevPod](https://devpod.sh).

Templates are fetched from [jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers)'s `templates/` directory.

## Install

    pipx install ./dvt

## Usage

    dvt template sync
    dvt template list
    dvt project init --template fastapi ./my-project
    dvt project add-feature agent      # run from inside a project with .devcontainer/devcontainer.json
    dvt up my-project
    dvt ssh my-project

## Development

    pixi install
    pixi run pytest
```

- [ ] **Step 4: Install the dev environment**

Run: `cd dvt && pixi install`
Expected: pixi resolves and creates `dvt/.pixi/` (already covered by the repo root `.gitignore`'s unanchored `.pixi/` / `pixi.lock` patterns — no new gitignore entries needed).

- [ ] **Step 5: Write the failing smoke test**

Create `dvt/tests/test_cli.py`:

```python
from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()


def test_cli_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd dvt && pixi run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.cli'` (the module doesn't exist yet).

- [ ] **Step 7: Create the minimal CLI stub**

Create `dvt/src/devtemplate/cli.py`:

```python
from __future__ import annotations

import typer

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd dvt && pixi run pytest tests/test_cli.py -v`
Expected: PASS (1 test)

- [ ] **Step 9: Commit**

```bash
git add dvt/pyproject.toml dvt/README.md dvt/src/devtemplate/__init__.py dvt/src/devtemplate/cli.py dvt/tests/test_cli.py
git commit -m "feat: scaffold dvt CLI package (pyproject.toml, entry point, smoke test)"
```

---

### Task 2: Settings (XDG paths, GitHub source config)

**Files:**
- Create: `dvt/src/devtemplate/config.py`
- Create: `dvt/tests/conftest.py`
- Test: `dvt/tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `devtemplate.config.Settings` — a pydantic-settings class with `.github_repo: str`, `.github_branch: str`, and properties `.data_dir: Path`, `.templates_dir: Path`, `.manifest_path: Path`. Also produces the shared pytest fixture `settings` (in `conftest.py`) that every later task's tests reuse to point `Settings()` at a temp directory instead of the real user data dir.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_config.py`:

```python
from devtemplate.config import Settings


def test_settings_default_github_source():
    settings = Settings()
    assert settings.github_repo == "jesserobertson/devcontainers"
    assert settings.github_branch == "main"


def test_settings_github_source_overridable_via_env(monkeypatch):
    monkeypatch.setenv("DVT_GITHUB_REPO", "someone/fork")
    monkeypatch.setenv("DVT_GITHUB_BRANCH", "dev")
    settings = Settings()
    assert settings.github_repo == "someone/fork"
    assert settings.github_branch == "dev"


def test_settings_paths_derive_from_data_dir(settings, tmp_path):
    assert settings.data_dir == tmp_path
    assert settings.templates_dir == tmp_path / "templates"
    assert settings.manifest_path == tmp_path / "manifest.json"
```

Create `dvt/tests/conftest.py`:

```python
from __future__ import annotations

import pytest

from devtemplate.config import Settings


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setattr(
        "devtemplate.config.platformdirs.user_data_dir", lambda name: str(tmp_path)
    )
    return Settings()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.config'`

- [ ] **Step 3: Implement Settings**

Create `dvt/src/devtemplate/config.py`:

```python
from __future__ import annotations

from pathlib import Path

import platformdirs
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DVT_")

    github_repo: str = "jesserobertson/devcontainers"
    github_branch: str = "main"

    @property
    def data_dir(self) -> Path:
        return Path(platformdirs.user_data_dir("dvt"))

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/config.py dvt/tests/conftest.py dvt/tests/test_config.py
git commit -m "feat: add dvt Settings (XDG paths via platformdirs, GitHub repo/branch config)"
```

---

### Task 3: DevContainerConfig pydantic model

**Files:**
- Create: `dvt/src/devtemplate/models.py`
- Test: `dvt/tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `devtemplate.models.DevContainerConfig` (extra fields allowed, no `Any`-typed fields so `hypothesis` can generate instances), `devtemplate.models.FeatureOptions`, `devtemplate.models.LifecycleCommand` — used by Task 5 (property tests).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError

from devtemplate.models import DevContainerConfig


def test_round_trip_preserves_explicitly_set_fields():
    data = {
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "runArgs": ["--gpus", "all"],
        "remoteUser": "dev",
    }
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data


def test_preserves_unknown_fields():
    data = {"customSetting": "value"}
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data


def test_rejects_non_dict_features():
    with pytest.raises(ValidationError):
        DevContainerConfig.model_validate({"features": "not-a-dict"})


def test_rejects_non_list_run_args():
    with pytest.raises(ValidationError):
        DevContainerConfig.model_validate({"runArgs": "not-a-list"})


def test_accepts_named_object_lifecycle_command():
    data = {"postCreateCommand": {"a": "cmd1", "b": ["cmd2", "arg"]}}
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.models'`

- [ ] **Step 3: Implement the model**

Create `dvt/src/devtemplate/models.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

FeatureOptions = dict[str, bool | int | str]
LifecycleCommand = str | list[str] | dict[str, str | list[str]]


class DevContainerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    image: str | None = None
    workspaceFolder: str | None = None
    workspaceMount: str | None = None
    features: dict[str, FeatureOptions] = {}
    runArgs: list[str] = []
    mounts: list[str] = []
    forwardPorts: list[int | str] = []
    remoteEnv: dict[str, str] = {}
    containerEnv: dict[str, str] = {}
    postCreateCommand: LifecycleCommand | None = None
    postStartCommand: LifecycleCommand | None = None
    postAttachCommand: LifecycleCommand | None = None
    onCreateCommand: LifecycleCommand | None = None
    updateContentCommand: LifecycleCommand | None = None
    initializeCommand: LifecycleCommand | None = None
    waitFor: str | None = None
    remoteUser: str | None = None
    shutdownAction: str | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/models.py dvt/tests/test_models.py
git commit -m "feat: add DevContainerConfig pydantic model"
```

---

### Task 4: Field-typed merge algorithm

**Files:**
- Create: `dvt/src/devtemplate/merge.py`
- Test: `dvt/tests/test_merge.py`

**Interfaces:**
- Consumes: nothing new (operates on plain `dict[str, Any]`, not the Task 3 model, so it stays a faithful, reusable port of `dev`'s generic `merge_layer` primitive).
- Produces: `devtemplate.merge.merge_layer(base: dict, overlay: dict) -> dict` and `devtemplate.merge.merge_layers(layers: list[dict]) -> dict`, both consumed by Task 5 (property tests) and Task 10 (`add-feature`).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_merge.py`:

```python
from devtemplate.merge import merge_layer


def test_scalar_field_overlay_wins():
    base = {"name": "old"}
    overlay = {"name": "new"}
    assert merge_layer(base, overlay)["name"] == "new"


def test_untouched_base_fields_preserved():
    base = {"image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    overlay = {"features": {"a": {}}}
    merged = merge_layer(base, overlay)
    assert merged["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_features_union():
    base = {"features": {"a": {}}}
    overlay = {"features": {"b": {}}}
    merged = merge_layer(base, overlay)
    assert merged["features"] == {"a": {}, "b": {}}


def test_features_overlay_wins_on_collision():
    base = {"features": {"a": {"x": 1}}}
    overlay = {"features": {"a": {"x": 2}}}
    merged = merge_layer(base, overlay)
    assert merged["features"]["a"] == {"x": 2}


def test_mounts_concatenate_with_dedup():
    base = {"mounts": ["m1", "m2"]}
    overlay = {"mounts": ["m2", "m3"]}
    merged = merge_layer(base, overlay)
    assert merged["mounts"] == ["m1", "m2", "m3"]


def test_run_args_concatenate_without_dedup():
    base = {"runArgs": ["--gpus", "all"]}
    overlay = {"runArgs": ["--gpus", "all"]}
    merged = merge_layer(base, overlay)
    assert merged["runArgs"] == ["--gpus", "all", "--gpus", "all"]


def test_lifecycle_named_object_forms_union():
    base = {"postCreateCommand": {"x": "cmd1"}}
    overlay = {"postCreateCommand": {"y": "cmd2"}}
    merged = merge_layer(base, overlay)
    assert merged["postCreateCommand"] == {"x": "cmd1", "y": "cmd2"}


def test_lifecycle_non_object_form_overlay_replaces():
    base = {"postCreateCommand": {"x": "cmd1"}}
    overlay = {"postCreateCommand": "pixi install"}
    merged = merge_layer(base, overlay)
    assert merged["postCreateCommand"] == "pixi install"


def test_map_fields_merge():
    base = {"remoteEnv": {"A": "1"}}
    overlay = {"remoteEnv": {"B": "2"}}
    merged = merge_layer(base, overlay)
    assert merged["remoteEnv"] == {"A": "1", "B": "2"}


def test_unknown_field_overlay_wins():
    base = {"customSetting": "old"}
    overlay = {"customSetting": "new"}
    assert merge_layer(base, overlay)["customSetting"] == "new"


def test_add_agent_to_fastapi_scenario():
    base = {
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "mounts": ["source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"],
        "postCreateCommand": "pixi install",
        "remoteUser": "dev",
    }
    overlay = {
        "name": "agent",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
        "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
        "mounts": ["source=agent-pixi-cache,target=/home/dev/.cache/pixi,type=volume"],
        "postCreateCommand": "pixi install",
        "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
        "waitFor": "postStartCommand",
        "remoteUser": "dev",
    }
    merged = merge_layer(base, overlay)
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
        "ghcr.io/jesserobertson/devcontainers/agent:latest": {},
    }
    assert merged["runArgs"] == ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]
    assert merged["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert merged["waitFor"] == "postStartCommand"
    assert merged["mounts"] == [
        "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume",
        "source=agent-pixi-cache,target=/home/dev/.cache/pixi,type=volume",
    ]
```

Note: this test does not assert on `merged["name"]` — `merge_layer` itself is the generic, faithful port of `dev`'s primitive where scalar fields are always overlay-wins by design. The project-identity-field stripping that makes `add-feature` safe to use in practice is a caller-side policy, tested separately in Task 10.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.merge'`

- [ ] **Step 3: Implement the merge algorithm**

Create `dvt/src/devtemplate/merge.py`:

```python
from __future__ import annotations

from typing import Any

SCALAR_FIELDS = {"name", "image", "remoteUser", "waitFor", "shutdownAction"}
LIFECYCLE_FIELDS = {
    "postCreateCommand",
    "postStartCommand",
    "postAttachCommand",
    "onCreateCommand",
    "updateContentCommand",
    "initializeCommand",
}
ARRAY_FIELDS = {"mounts", "forwardPorts"}
ARRAY_CONCAT_FIELDS = {"runArgs"}
MAP_FIELDS = {"remoteEnv", "containerEnv"}
FEATURE_FIELDS = {"features"}


def merge_layer(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay onto base using field-type rules. Overlay is the higher-priority layer."""
    result = dict(base)
    for key, overlay_value in overlay.items():
        if key in SCALAR_FIELDS:
            result[key] = overlay_value
        elif key in LIFECYCLE_FIELDS:
            result[key] = _merge_lifecycle_command(result.get(key), overlay_value)
        elif key in FEATURE_FIELDS:
            result[key] = _merge_feature_map(result.get(key), overlay_value)
        elif key in ARRAY_CONCAT_FIELDS:
            result[key] = _merge_array_concat(result.get(key), overlay_value)
        elif key in ARRAY_FIELDS:
            result[key] = _merge_array_dedup(result.get(key), overlay_value)
        elif key in MAP_FIELDS:
            result[key] = _merge_map(result.get(key), overlay_value)
        else:
            result[key] = overlay_value
    return result


def merge_layers(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compose N layers in order (first = lowest priority, last = highest priority)."""
    result: dict[str, Any] = {}
    for layer in layers:
        result = merge_layer(result, layer)
    return result


def _merge_lifecycle_command(base_value: Any, overlay_value: Any) -> Any:
    if isinstance(overlay_value, dict) and isinstance(base_value, dict):
        merged = dict(base_value)
        merged.update(overlay_value)
        return merged
    return overlay_value


def _merge_feature_map(base_value: Any, overlay_value: Any) -> Any:
    if not isinstance(overlay_value, dict):
        return base_value
    merged = dict(base_value) if isinstance(base_value, dict) else {}
    merged.update(overlay_value)
    return merged


def _merge_array_dedup(base_value: Any, overlay_value: Any) -> list[Any]:
    if not isinstance(overlay_value, list):
        return list(base_value) if isinstance(base_value, list) else []
    merged = list(base_value) if isinstance(base_value, list) else []
    for item in overlay_value:
        if item not in merged:
            merged.append(item)
    return merged


def _merge_array_concat(base_value: Any, overlay_value: Any) -> list[Any]:
    if not isinstance(overlay_value, list):
        return list(base_value) if isinstance(base_value, list) else []
    merged = list(base_value) if isinstance(base_value, list) else []
    merged.extend(overlay_value)
    return merged


def _merge_map(base_value: Any, overlay_value: Any) -> dict[str, Any]:
    if not isinstance(overlay_value, dict):
        return dict(base_value) if isinstance(base_value, dict) else {}
    merged = dict(base_value) if isinstance(base_value, dict) else {}
    merged.update(overlay_value)
    return merged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_merge.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/merge.py dvt/tests/test_merge.py
git commit -m "feat: add field-typed devcontainer.json merge algorithm"
```

---

### Task 5: Hypothesis property tests for the merge algorithm

**Files:**
- Test: `dvt/tests/test_merge_properties.py`

**Interfaces:**
- Consumes: `devtemplate.merge.merge_layer` (Task 4), `devtemplate.models.DevContainerConfig` (Task 3) — via `hypothesis`'s pydantic integration (`st.from_type(DevContainerConfig)`), which generates arbitrary valid instances directly since every field on the model has a concrete (non-`Any`) type.
- Produces: nothing new consumed by later tasks — this task only adds test coverage for Task 4's existing implementation.

- [ ] **Step 1: Write the property tests**

Create `dvt/tests/test_merge_properties.py`:

```python
from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from devtemplate.merge import merge_layer
from devtemplate.models import DevContainerConfig


@given(st.from_type(DevContainerConfig))
def test_merge_idempotent_except_run_args(config: DevContainerConfig) -> None:
    # runArgs intentionally concatenates without dedup (repeated flags like
    # --env-file are legitimate), so it is excluded from this idempotence
    # property and covered separately below.
    data = config.model_dump(exclude_defaults=True)
    merged = merge_layer(data, data)
    merged.pop("runArgs", None)
    comparable = dict(data)
    comparable.pop("runArgs", None)
    assert merged == comparable


def test_run_args_duplicates_on_repeat_by_design() -> None:
    data = {"runArgs": ["--gpus", "all"]}
    merged = merge_layer(data, data)
    assert merged["runArgs"] == ["--gpus", "all", "--gpus", "all"]


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_features_union_never_drops_a_key(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    merged_features = merge_layer(base_data, overlay_data).get("features", {})
    for key in base_data.get("features", {}):
        assert key in merged_features
    for key in overlay_data.get("features", {}):
        assert key in merged_features


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_run_args_concatenates_without_dropping(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    merged = merge_layer(base_data, overlay_data)
    expected_length = len(base_data.get("runArgs", [])) + len(overlay_data.get("runArgs", []))
    assert len(merged.get("runArgs", [])) == expected_length


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_mounts_never_produces_duplicates(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    mounts = merge_layer(base_data, overlay_data).get("mounts", [])
    assert len(mounts) == len(set(mounts))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_merge_properties.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.merge_properties'` is not applicable here since the file is new but imports existing modules; actual expected failure is `ModuleNotFoundError` only if run before Task 4 exists. Since Task 4 already shipped `merge.py` and `models.py` exists from Task 3, this step should instead simply confirm the tests run and pass on first try. Skip the fail-first ritual for this task — go straight to Step 3.

- [ ] **Step 3: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_merge_properties.py -v`
Expected: PASS (5 tests, each run across hypothesis's default 100 generated examples)

- [ ] **Step 4: Commit**

```bash
git add dvt/tests/test_merge_properties.py
git commit -m "test: add hypothesis property tests for the merge algorithm"
```

---

### Task 6: GitHub template fetch

**Files:**
- Create: `dvt/src/devtemplate/github.py`
- Test: `dvt/tests/test_github.py`

**Interfaces:**
- Consumes: nothing new (takes `repo`/`branch` as plain strings, not a `Settings` object, keeping it independently testable and reusable).
- Produces: `devtemplate.github.list_template_names(client: httpx.Client, repo: str, branch: str) -> list[str]` and `devtemplate.github.fetch_template(client: httpx.Client, repo: str, branch: str, name: str) -> dict` — both consumed by Task 7 (`store.py`).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_github.py`:

```python
import httpx
import pytest

from devtemplate.github import fetch_template, list_template_names


def test_list_template_names_returns_only_directories():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"name": "fastapi", "type": "dir"},
                {"name": "README.md", "type": "file"},
                {"name": "agent", "type": "dir"},
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    names = list_template_names(client, "jesserobertson/devcontainers", "main")
    assert names == ["agent", "fastapi"]


def test_list_template_names_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        list_template_names(client, "jesserobertson/devcontainers", "main")


def test_fetch_template_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "fastapi", "image": "ghcr.io/x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    template = fetch_template(client, "jesserobertson/devcontainers", "main", "fastapi")
    assert template == {"name": "fastapi", "image": "ghcr.io/x"}


def test_fetch_template_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_template(client, "jesserobertson/devcontainers", "main", "nonexistent")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_github.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.github'`

- [ ] **Step 3: Implement the fetch functions**

Create `dvt/src/devtemplate/github.py`:

```python
from __future__ import annotations

import json
from typing import Any

import httpx


def list_template_names(client: httpx.Client, repo: str, branch: str) -> list[str]:
    url = f"https://api.github.com/repos/{repo}/contents/templates?ref={branch}"
    response = client.get(url)
    response.raise_for_status()
    entries = response.json()
    return sorted(entry["name"] for entry in entries if entry["type"] == "dir")


def fetch_template(client: httpx.Client, repo: str, branch: str, name: str) -> dict[str, Any]:
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/templates/{name}/devcontainer.json"
    response = client.get(url)
    response.raise_for_status()
    return json.loads(response.text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_github.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/github.py dvt/tests/test_github.py
git commit -m "feat: add GitHub template fetch (Contents API + raw downloads)"
```

---

### Task 7: Local template store and sync

**Files:**
- Create: `dvt/src/devtemplate/store.py`
- Test: `dvt/tests/test_store.py`

**Interfaces:**
- Consumes: `devtemplate.config.Settings` (Task 2), `devtemplate.github.list_template_names` / `fetch_template` (Task 6), the `settings` pytest fixture (Task 2's `conftest.py`).
- Produces: `devtemplate.store.sync_templates(settings, client) -> list[str]`, `devtemplate.store.list_cached_templates(settings) -> list[str]`, `devtemplate.store.load_cached_template(settings, name) -> dict`, `devtemplate.store.read_manifest(settings) -> list[str]` — all consumed by Tasks 8, 9, 10.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_store.py`:

```python
import httpx
import pytest

from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    read_manifest,
    sync_templates,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_writes_templates_and_manifest(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/templates"):
            return httpx.Response(200, json=[{"name": "fastapi", "type": "dir"}])
        return httpx.Response(200, json={"name": "fastapi", "image": "ghcr.io/x"})

    names = sync_templates(settings, _client(handler))

    assert names == ["fastapi"]
    assert list_cached_templates(settings) == ["fastapi"]
    assert load_cached_template(settings, "fastapi") == {"name": "fastapi", "image": "ghcr.io/x"}
    assert read_manifest(settings) == ["fastapi"]


def test_sync_does_not_touch_custom_template_dirs(settings):
    custom_dir = settings.templates_dir / "my-custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "devcontainer.json").write_text('{"name": "custom"}')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/templates"):
            return httpx.Response(200, json=[{"name": "fastapi", "type": "dir"}])
        return httpx.Response(200, json={"name": "fastapi"})

    sync_templates(settings, _client(handler))

    assert (custom_dir / "devcontainer.json").read_text() == '{"name": "custom"}'
    assert "my-custom" not in read_manifest(settings)


def test_load_cached_template_missing_raises(settings):
    with pytest.raises(FileNotFoundError):
        load_cached_template(settings, "nonexistent")


def test_list_cached_templates_empty_before_sync(settings):
    assert list_cached_templates(settings) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.store'`

- [ ] **Step 3: Implement the store**

Create `dvt/src/devtemplate/store.py`:

```python
from __future__ import annotations

import json

import httpx

from devtemplate.config import Settings
from devtemplate.github import fetch_template, list_template_names

MANIFEST_KEY = "managed_templates"


def read_manifest(settings: Settings) -> list[str]:
    if not settings.manifest_path.exists():
        return []
    data = json.loads(settings.manifest_path.read_text())
    return data.get(MANIFEST_KEY, [])


def write_manifest(settings: Settings, managed_templates: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(
        json.dumps({MANIFEST_KEY: sorted(managed_templates)}, indent=2)
    )


def sync_templates(settings: Settings, client: httpx.Client) -> list[str]:
    """Fetch every template listed under templates/ on GitHub into the local cache.

    Only ever writes to the names GitHub currently lists, so any custom template
    directories a user has dropped in by hand under a different name are never touched.
    """
    names = list_template_names(client, settings.github_repo, settings.github_branch)
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        template = fetch_template(client, settings.github_repo, settings.github_branch, name)
        template_dir = settings.templates_dir / name
        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / "devcontainer.json").write_text(json.dumps(template, indent=2))
    write_manifest(settings, names)
    return names


def list_cached_templates(settings: Settings) -> list[str]:
    if not settings.templates_dir.exists():
        return []
    return sorted(p.name for p in settings.templates_dir.iterdir() if p.is_dir())


def load_cached_template(settings: Settings, name: str) -> dict:
    path = settings.templates_dir / name / "devcontainer.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached template named {name!r}. Run 'dvt template sync' first."
        )
    return json.loads(path.read_text())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/store.py dvt/tests/test_store.py
git commit -m "feat: add local template store and sync orchestration"
```

---

### Task 8: `dvt template list/show/sync` commands

**Files:**
- Create: `dvt/src/devtemplate/commands/__init__.py`
- Create: `dvt/src/devtemplate/commands/template.py`
- Test: `dvt/tests/test_template_command.py`

**Interfaces:**
- Consumes: `devtemplate.config.Settings` (Task 2), `devtemplate.store.list_cached_templates` / `load_cached_template` / `sync_templates` (Task 7), the `settings` fixture (Task 2).
- Produces: `devtemplate.commands.template.app` — a Typer sub-app with `list`, `show`, `sync` commands, registered onto the root app in Task 11.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_template_command.py`:

```python
import json

from typer.testing import CliRunner

from devtemplate.commands.template import app

runner = CliRunner()


def test_list_reports_no_templates_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached templates" in result.stdout


def test_list_shows_cached_template_names(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi", "image": "ghcr.io/x", "features": {"a": {}}})
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_show_prints_cached_template(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )

    result = runner.invoke(app, ["show", "fastapi"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_sync_reports_synced_template_names(settings, monkeypatch):
    monkeypatch.setattr(
        "devtemplate.commands.template.sync_templates",
        lambda settings_arg, client: ["fastapi", "agent"],
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_template_command.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.commands'`

- [ ] **Step 3: Implement the command group**

Create `dvt/src/devtemplate/commands/__init__.py` (empty):

```python
```

Create `dvt/src/devtemplate/commands/template.py`:

```python
from __future__ import annotations

import json

import httpx
import typer
from rich.console import Console
from rich.table import Table

from devtemplate.config import Settings
from devtemplate.store import list_cached_templates, load_cached_template, sync_templates

app = typer.Typer(help="Inspect and refresh cached devcontainer templates.")
console = Console()


@app.command("list")
def list_templates() -> None:
    settings = Settings()
    names = list_cached_templates(settings)
    if not names:
        console.print("No cached templates. Run 'dvt template sync' first.")
        raise typer.Exit(code=0)
    table = Table("Name", "Image", "Features")
    for name in names:
        template = load_cached_template(settings, name)
        table.add_row(
            name,
            template.get("image", "?"),
            ", ".join(template.get("features", {}).keys()),
        )
    console.print(table)


@app.command("show")
def show_template(name: str) -> None:
    settings = Settings()
    template = load_cached_template(settings, name)
    console.print_json(json.dumps(template))


@app.command("sync")
def sync() -> None:
    settings = Settings()
    with httpx.Client() as client:
        names = sync_templates(settings, client)
    console.print(f"Synced {len(names)} templates: {', '.join(names)}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_template_command.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/__init__.py dvt/src/devtemplate/commands/template.py dvt/tests/test_template_command.py
git commit -m "feat: add dvt template list/show/sync commands"
```

---

### Task 9: `dvt project init` command

**Files:**
- Create: `dvt/src/devtemplate/commands/project.py`
- Test: `dvt/tests/test_project_command.py`

**Interfaces:**
- Consumes: `devtemplate.config.Settings` (Task 2), `devtemplate.store.list_cached_templates` / `load_cached_template` / `sync_templates` (Task 7).
- Produces: `devtemplate.commands.project.app` with an `init` command, registered onto the root app in Task 11. Task 10 adds `add-feature` to this same file/app.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_project_command.py`:

```python
import json

from typer.testing import CliRunner

from devtemplate.commands.project import app

runner = CliRunner()


def test_init_scaffolds_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"})
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    target = project_dir / ".devcontainer" / "devcontainer.json"
    assert target.exists()
    assert json.loads(target.read_text())["name"] == "fastapi"


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "fastapi"}))

    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text('{"name": "existing"}')

    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 1
    assert json.loads((project_dir / ".devcontainer" / "devcontainer.json").read_text())["name"] == "existing"


def test_init_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "fastapi"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(json.dumps({"name": "fastapi"}))
        return ["fastapi"]

    monkeypatch.setattr("devtemplate.commands.project.sync_templates", fake_sync)

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert (project_dir / ".devcontainer" / "devcontainer.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_project_command.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.commands.project'`

- [ ] **Step 3: Implement the `init` command**

Create `dvt/src/devtemplate/commands/project.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer
from rich.console import Console

from devtemplate.config import Settings
from devtemplate.store import list_cached_templates, load_cached_template, sync_templates

app = typer.Typer(help="Scaffold and evolve a project's devcontainer.json from templates.")
console = Console()


@app.command("init")
def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),
    template: str = typer.Option(..., "--template", help="Cached template name to scaffold from."),
    refresh: bool = typer.Option(False, "--refresh", help="Sync templates from GitHub before scaffolding."),
) -> None:
    settings = Settings()
    if refresh or not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_templates(settings, client)

    config = load_cached_template(settings, template)
    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{target} already exists.[/red] "
            "Use 'dvt project add-feature' to layer onto it instead."
        )
        raise typer.Exit(code=1)
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target} from template '{template}'.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_project_command.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/project.py dvt/tests/test_project_command.py
git commit -m "feat: add dvt project init command"
```

---

### Task 10: `dvt project add-feature` command

**Files:**
- Modify: `dvt/src/devtemplate/commands/project.py`
- Modify: `dvt/tests/test_project_command.py`

**Interfaces:**
- Consumes: `devtemplate.merge.merge_layer` (Task 4), `devtemplate.store.load_cached_template` (Task 7).
- Produces: `add-feature` command on the existing `devtemplate.commands.project.app` (Task 9) — nothing new consumed by later tasks beyond the already-registered `app`.

- [ ] **Step 1: Write the failing tests**

Add to `dvt/tests/test_project_command.py`:

```python
def test_add_feature_merges_into_existing_devcontainer_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps({
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "remoteUser": "dev",
    }))

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({
        "name": "agent",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "workspaceFolder": "/workspace",
        "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
        "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
        "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
        "waitFor": "postStartCommand",
        "remoteUser": "dev",
    }))

    result = runner.invoke(app, ["add-feature", "agent"])
    assert result.exit_code == 0

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


def test_add_feature_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add-feature", "agent"])
    assert result.exit_code == 1


def test_add_feature_refuses_on_invalid_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = '{\n  // a comment\n  "name": "my-project"\n}'
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["add-feature", "agent"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_project_command.py -v -k add_feature`
Expected: FAIL — `AssertionError` from Typer reporting "No such command 'add-feature'" (exit code 2, not 0/1 as asserted).

- [ ] **Step 3: Implement `add-feature`**

Add to `dvt/src/devtemplate/commands/project.py` (after the existing imports, add `merge_layer`; after the `init` command, add):

```python
from devtemplate.merge import merge_layer
```

(Insert this import alongside the existing `from devtemplate.store import ...` line.)

```python
IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount"}


@app.command("add-feature")
def add_feature(name: str) -> None:
    settings = Settings()
    target = Path(".devcontainer") / "devcontainer.json"
    if not target.exists():
        console.print(f"[red]{target} not found.[/red] Run 'dvt project init' first.")
        raise typer.Exit(code=1)

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError:
        console.print(
            f"[red]{target} is not strict JSON (comments/trailing commas are not supported).[/red] "
            "Add this feature's devcontainer.json snippet by hand instead."
        )
        raise typer.Exit(code=1)

    template = load_cached_template(settings, name)
    overlay = {key: value for key, value in template.items() if key not in IDENTITY_FIELDS}
    merged = merge_layer(base_config, overlay)
    target.write_text(json.dumps(merged, indent=2) + "\n")
    console.print(f"Merged feature '{name}' into {target}.")
```

The full file after this step:

```python
from __future__ import annotations

import json
from pathlib import Path

import httpx
import typer
from rich.console import Console

from devtemplate.config import Settings
from devtemplate.merge import merge_layer
from devtemplate.store import list_cached_templates, load_cached_template, sync_templates

app = typer.Typer(help="Scaffold and evolve a project's devcontainer.json from templates.")
console = Console()

IDENTITY_FIELDS = {"name", "workspaceFolder", "workspaceMount"}


@app.command("init")
def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),
    template: str = typer.Option(..., "--template", help="Cached template name to scaffold from."),
    refresh: bool = typer.Option(False, "--refresh", help="Sync templates from GitHub before scaffolding."),
) -> None:
    settings = Settings()
    if refresh or not list_cached_templates(settings):
        with httpx.Client() as client:
            sync_templates(settings, client)

    config = load_cached_template(settings, template)
    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        console.print(
            f"[red]{target} already exists.[/red] "
            "Use 'dvt project add-feature' to layer onto it instead."
        )
        raise typer.Exit(code=1)
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")
    console.print(f"Scaffolded {target} from template '{template}'.")


@app.command("add-feature")
def add_feature(name: str) -> None:
    settings = Settings()
    target = Path(".devcontainer") / "devcontainer.json"
    if not target.exists():
        console.print(f"[red]{target} not found.[/red] Run 'dvt project init' first.")
        raise typer.Exit(code=1)

    try:
        base_config = json.loads(target.read_text())
    except json.JSONDecodeError:
        console.print(
            f"[red]{target} is not strict JSON (comments/trailing commas are not supported).[/red] "
            "Add this feature's devcontainer.json snippet by hand instead."
        )
        raise typer.Exit(code=1)

    template = load_cached_template(settings, name)
    overlay = {key: value for key, value in template.items() if key not in IDENTITY_FIELDS}
    merged = merge_layer(base_config, overlay)
    target.write_text(json.dumps(merged, indent=2) + "\n")
    console.print(f"Merged feature '{name}' into {target}.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_project_command.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/project.py dvt/tests/test_project_command.py
git commit -m "feat: add dvt project add-feature command"
```

---

### Task 11: Wire up the root CLI (devpod passthroughs + subcommands)

**Files:**
- Modify: `dvt/src/devtemplate/cli.py`
- Test: `dvt/tests/test_cli.py`

**Interfaces:**
- Consumes: `devtemplate.commands.template.app` (Task 8), `devtemplate.commands.project.app` (Tasks 9-10).
- Produces: the final `devtemplate.cli.app` — the complete command surface this plan builds toward. Nothing else consumes this; it's the plan's last task.

- [ ] **Step 1: Write the failing tests**

Add to `dvt/tests/test_cli.py`:

```python
def test_up_invokes_devpod_up(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert calls == [["devpod", "up", "my-project"]]


def test_ssh_forwards_extra_args(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = runner.invoke(cli_module.app, ["ssh", "my-project", "--", "ls", "-la"])

    assert result.exit_code == 0
    assert calls == [["devpod", "ssh", "my-project", "ls", "-la"]]


def test_stop_and_delete_invoke_devpod(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    runner.invoke(cli_module.app, ["stop", "my-project"])
    runner.invoke(cli_module.app, ["delete", "my-project"])

    assert calls == [["devpod", "stop", "my-project"], ["devpod", "delete", "my-project"]]


def test_template_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["template", "--help"])
    assert result.exit_code == 0


def test_project_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["project", "--help"])
    assert result.exit_code == 0
```

The top of `dvt/tests/test_cli.py` (`from typer.testing import CliRunner` / `from devtemplate.cli import app` / `runner = CliRunner()`) already exists from Task 1 — leave it as-is.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd dvt && pixi run pytest tests/test_cli.py -v`
Expected: FAIL — `AssertionError` / Typer "No such command 'up'" (exit code 2) for the passthrough tests, and "No such command 'template'"/"'project'" for the subcommand tests.

- [ ] **Step 3: Implement the full CLI**

Replace `dvt/src/devtemplate/cli.py`:

```python
from __future__ import annotations

import subprocess

import typer

from devtemplate.commands import project, template

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")
app.add_typer(template.app, name="template")
app.add_typer(project.app, name="project")


def _devpod_passthrough(subcommand: str, name: str, extra_args: list[str]) -> None:
    result = subprocess.run(["devpod", subcommand, name, *extra_args])
    raise typer.Exit(code=result.returncode)


@app.command()
def up(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod up."),
) -> None:
    """Passthrough to `devpod up`."""
    _devpod_passthrough("up", name, extra_args or [])


@app.command()
def ssh(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod ssh."),
) -> None:
    """Passthrough to `devpod ssh`."""
    _devpod_passthrough("ssh", name, extra_args or [])


@app.command()
def stop(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod stop."),
) -> None:
    """Passthrough to `devpod stop`."""
    _devpod_passthrough("stop", name, extra_args or [])


@app.command()
def delete(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod delete."),
) -> None:
    """Passthrough to `devpod delete`."""
    _devpod_passthrough("delete", name, extra_args or [])


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd dvt && pixi run pytest tests/test_cli.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full test suite**

Run: `cd dvt && pixi run pytest -v`
Expected: PASS — every test from Tasks 1–11 (config, models, merge fixtures + properties, github, store, template command, project command, cli) green, nothing regressed.

- [ ] **Step 6: Commit**

```bash
git add dvt/src/devtemplate/cli.py dvt/tests/test_cli.py
git commit -m "feat: wire up dvt CLI with devpod passthroughs and subcommands"
```

---

## Self-Review Notes

- **Spec coverage:** Package layout & naming (Task 1) ✓, config/XDG (Task 2) ✓, template sync via GitHub Contents API + raw downloads with manifest-scoped safety (Tasks 6–7) ✓, command surface — template list/show/sync (Task 8), project init (Task 9), project add-feature (Task 10), up/ssh/stop/delete passthroughs (Task 11) ✓, merge algorithm ported from `dev`'s field-typed rules (Task 4) ✓, JSON handling — strict parse, refuse-and-print on failure (Task 10) ✓, pydantic model testing conventions — round-trip, `ValidationError` cases (Task 3) ✓, hypothesis property tests — idempotence (with the `runArgs` carve-out), features-union-never-drops, runArgs-concatenation-length, mounts-no-duplicates (Task 5) ✓, distribution (pipx + pixi dev loop) baked into Task 1's `pyproject.toml` ✓.
- **Deviation from spec text, called out explicitly:** the spec's merge-algorithm bullet says "merging is idempotent" without qualification; Task 5 implements and tests the correct, narrower version (idempotent for every field except `runArgs`, which by design concatenates without dedup even on repeat application) rather than the imprecise blanket claim.
- **New behavior beyond the spec's literal text, needed for correctness:** `IDENTITY_FIELDS` stripping in `add_feature` (Task 10). The spec's merge section didn't call this out explicitly, but it follows directly from adopting `dev`'s scalar-overlay-wins rule verbatim — without stripping `name`/`workspaceFolder`/`workspaceMount`, adding any feature would silently rename the target project to that feature's own template name. Covered by `test_add_feature_merges_into_existing_devcontainer_json`.
- **Placeholder scan:** no TBD/TODO; every step shows complete file content or exact code to insert.
- **Type consistency:** `Settings`, `DevContainerConfig`, `merge_layer`/`merge_layers`, `list_template_names`/`fetch_template`, `sync_templates`/`list_cached_templates`/`load_cached_template`/`read_manifest` are each defined once (Tasks 2, 3, 4, 6, 7 respectively) and reused verbatim (same names, same signatures) in every later task that imports them.
