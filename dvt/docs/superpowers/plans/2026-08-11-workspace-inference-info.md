# dvt info + cwd-based workspace inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `name` optional on `dvt up`/`ssh`/`stop`/`delete` (inferring it from the current
directory's `devcontainer.local_folder` container label when omitted), and add a new `dvt info`
command that shows a project's devcontainer config plus any live workspace tied to it.

**Architecture:** Every workspace `dvt up` builds already carries a `devcontainer.local_folder`
label (the absolute host path it was built from — see `container.py::compute_labels`), the same
label VS Code's own "Attach to Running Container" uses. A new `find_workspace_containers_by_folder`
sibling to the existing `find_workspace_container` looks workspaces up by that label instead of
by name. Two small resolver functions in a new `workspace_lookup.py` turn an optional CLI
argument into a concrete name: one match reuses it, multiple refuses and lists candidates, and
zero either falls back to the directory's own name (for `up`, to create a fresh workspace) or
refuses (for `ssh`/`stop`/`delete`, which only ever act on something that already exists). `dvt
info` is a new, argument-free top-level command (matching `dvt feature`'s existing "always
operates on the current directory" pattern) that reads `devcontainer.json`/`dvt-features.json`
locally, then best-effort reports live status via the same folder-based lookup.

**Tech Stack:** Python 3.12, Typer (CLI), Rich (console output), `logerr` (Result-typed error
handling), `docker`-py (container introspection via a shared `docker.DockerClient` for both
Docker and Podman), pytest.

## Global Constraints

- `dvt info` calls `get_client(...)` with `podman_machine_auto_init=False,
  podman_machine_auto_start=False` explicitly, regardless of the user's configured `Settings` —
  deliberately fast and passive; it must never block for minutes starting a stopped Podman
  machine just to show local config. (These flags only affect the Windows Podman machine
  provisioning path — an already-running Podman or Docker connects the same either way.)
- Every Typer command, argument, and option needs non-empty `help=` text — enforced by
  `tests/test_cli_help.py`, which must keep passing unmodified.
- `up`/`ssh`/`stop`/`delete`'s existing explicit-name behavior must be byte-for-byte unchanged —
  every existing test in `tests/test_cli.py` that passes a name explicitly must keep passing
  without modification.
- `devcontainer.local_folder` label comparisons must resolve the folder via `.resolve()` before
  filtering, matching exactly how `compute_labels` writes it (`str(project_path.resolve())`) —
  otherwise a relative-vs-absolute or differently-cased Windows path mismatch would silently
  never match.
- Run `pixi run test unit` and `pixi run quality check` from `dvt/` after each task; both must
  pass before moving on.

---

### Task 1: `find_workspace_containers_by_folder`

**Files:**
- Modify: `src/devtemplate/container.py` (add a function after `find_workspace_container`,
  currently ending at line 225)
- Test: `tests/test_container.py` (append)

**Interfaces:**
- Produces: `find_workspace_containers_by_folder(client: DockerClient, folder: Path) ->
  list[Container]` — used by Task 2's resolvers and Task 3's `info` command.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_container.py`:

```python
def test_find_workspace_containers_by_folder_filters_by_label(tmp_path):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_client.containers.list.return_value = [fake_container]

    found = find_workspace_containers_by_folder(fake_client, tmp_path)

    assert found == [fake_container]
    fake_client.containers.list.assert_called_once_with(
        all=True,
        filters={"label": f"devcontainer.local_folder={tmp_path.resolve()}"},
    )


def test_find_workspace_containers_by_folder_returns_empty_list_when_absent(tmp_path):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    assert find_workspace_containers_by_folder(fake_client, tmp_path) == []
```

Add `find_workspace_containers_by_folder` to the existing
`from devtemplate.container import (...)` import line at the top of the file (alongside
whatever's already imported there, e.g. `find_workspace_container`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_container.py -k containers_by_folder -v`
Expected: FAIL with `ImportError: cannot import name 'find_workspace_containers_by_folder'`

- [ ] **Step 3: Implement `find_workspace_containers_by_folder`**

Add to `src/devtemplate/container.py`, directly after `find_workspace_container`:

```python
def find_workspace_containers_by_folder(
    client: DockerClient, folder: Path
) -> list[Container]:
    """Find every container tagged devcontainer.local_folder=folder (resolved to an
    absolute path the same way compute_labels wrote it), regardless of its own
    dvt.workspace name - lets a caller recognize a workspace tied to this folder
    even if it was created under a name that doesn't match the folder's own."""
    return client.containers.list(
        all=True,
        filters={"label": f"devcontainer.local_folder={folder.resolve()}"},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_container.py -v`
Expected: PASS (all tests in the file, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/container.py tests/test_container.py
git commit -m "feat(dvt): add find_workspace_containers_by_folder"
```

---

### Task 2: `workspace_lookup.py` — name resolution

**Files:**
- Create: `src/devtemplate/workspace_lookup.py`
- Test: `tests/test_workspace_lookup.py`

**Interfaces:**
- Consumes: `find_workspace_containers_by_folder` from `devtemplate.container` (Task 1).
- Produces: `resolve_for_up(client: DockerClient, name: str | None, cwd: Path) ->
  Result[str, Exception]` and `resolve_existing(client: DockerClient, name: str | None,
  cwd: Path, command: str) -> Result[str, Exception]` — used by Task 4's `cli.py` wiring.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workspace_lookup.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from devtemplate.workspace_lookup import resolve_existing, resolve_for_up


def _fake_client_with_workspaces(names: list[str]) -> MagicMock:
    client = MagicMock()
    client.containers.list.return_value = [
        MagicMock(labels={"dvt.workspace": name, "devcontainer.local_folder": "x"})
        for name in names
    ]
    return client


def test_resolve_for_up_passes_through_explicit_name_without_any_lookup(tmp_path):
    client = MagicMock()

    result = resolve_for_up(client, "explicit", tmp_path)

    assert result.unwrap() == "explicit"
    client.containers.list.assert_not_called()


def test_resolve_for_up_reuses_the_single_matching_workspace(tmp_path):
    client = _fake_client_with_workspaces(["my-custom-name"])

    result = resolve_for_up(client, None, tmp_path)

    assert result.unwrap() == "my-custom-name"


def test_resolve_for_up_falls_back_to_directory_name_when_no_match(tmp_path):
    client = _fake_client_with_workspaces([])

    result = resolve_for_up(client, None, tmp_path)

    assert result.unwrap() == tmp_path.resolve().name


def test_resolve_for_up_refuses_on_multiple_matches(tmp_path):
    client = _fake_client_with_workspaces(["bar", "foo"])

    result = resolve_for_up(client, None, tmp_path)

    assert result.is_err()
    message = str(result.unwrap_err())
    assert "bar" in message
    assert "foo" in message
    assert "dvt up <name>" in message


def test_resolve_existing_passes_through_explicit_name_without_any_lookup(tmp_path):
    client = MagicMock()

    result = resolve_existing(client, "explicit", tmp_path, "ssh")

    assert result.unwrap() == "explicit"
    client.containers.list.assert_not_called()


def test_resolve_existing_uses_the_single_matching_workspace(tmp_path):
    client = _fake_client_with_workspaces(["my-custom-name"])

    result = resolve_existing(client, None, tmp_path, "ssh")

    assert result.unwrap() == "my-custom-name"


def test_resolve_existing_refuses_when_no_match(tmp_path):
    client = _fake_client_with_workspaces([])

    result = resolve_existing(client, None, tmp_path, "ssh")

    assert result.is_err()
    assert "No workspace found" in str(result.unwrap_err())


def test_resolve_existing_refuses_on_multiple_matches_naming_the_given_command(tmp_path):
    client = _fake_client_with_workspaces(["bar", "foo"])

    result = resolve_existing(client, None, tmp_path, "stop")

    assert result.is_err()
    message = str(result.unwrap_err())
    assert "bar" in message
    assert "foo" in message
    assert "dvt stop <name>" in message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_workspace_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.workspace_lookup'`

- [ ] **Step 3: Implement `workspace_lookup.py`**

Create `src/devtemplate/workspace_lookup.py`:

```python
from __future__ import annotations

from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate.container import find_workspace_containers_by_folder


def _names_by_folder(client: DockerClient, cwd: Path) -> list[str]:
    containers = find_workspace_containers_by_folder(client, cwd)
    return sorted(
        name
        for name in (container.labels.get("dvt.workspace") for container in containers)
        if name
    )


def _multiple_matches_error(command: str, names: list[str]) -> Exception:
    return ValueError(
        f"Multiple workspaces match this folder: {', '.join(names)}. "
        f"Run 'dvt {command} <name>' with one of these."
    )


def resolve_for_up(
    client: DockerClient, name: str | None, cwd: Path
) -> Result[str, Exception]:
    """Turn dvt up's optional name into a concrete one. An explicit name passes
    through unchanged. When omitted: exactly one workspace already tied to this
    folder (via its devcontainer.local_folder label) reuses that name; none yet
    falls back to the folder's own directory name, to create a fresh workspace
    (matching dvt init's own default-name derivation); more than one refuses,
    listing every candidate, since dvt won't guess which one you meant.
    """
    if name is not None:
        return Ok(name)
    try:
        names = _names_by_folder(client, cwd)
    except Exception as exc:
        return Err(exc)
    if len(names) == 1:
        return Ok(names[0])
    if not names:
        return Ok(cwd.resolve().name)
    return Err(_multiple_matches_error("up", names))


def resolve_existing(
    client: DockerClient, name: str | None, cwd: Path, command: str
) -> Result[str, Exception]:
    """Same shape as resolve_for_up, for commands that only ever act on a
    workspace that already exists (ssh/stop/delete) - so no matches is also a
    refusal, not a directory-name fallback. `command` names the actual command
    that was run, so the refusal's suggested next step is accurate.
    """
    if name is not None:
        return Ok(name)
    try:
        names = _names_by_folder(client, cwd)
    except Exception as exc:
        return Err(exc)
    if len(names) == 1:
        return Ok(names[0])
    if not names:
        return Err(
            ValueError(
                "No workspace found for this folder. Specify a name, "
                "or run 'dvt up' to create one."
            )
        )
    return Err(_multiple_matches_error(command, names))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_workspace_lookup.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/workspace_lookup.py tests/test_workspace_lookup.py
git commit -m "feat(dvt): add resolve_for_up/resolve_existing workspace-name resolvers"
```

---

### Task 3: `dvt info` command

**Files:**
- Create: `src/devtemplate/commands/info.py`
- Test: `tests/test_info_command.py`

**Interfaces:**
- Consumes: `find_workspace_containers_by_folder` from `devtemplate.container` (Task 1),
  `load_sidecar` from `devtemplate.sidecar`, `get_client` from `devtemplate.runtime`,
  `load_settings` from `devtemplate.config`.
- Produces: `info() -> None` — a plain Typer-decoratable function taking no arguments (no
  `Typer()` app of its own), registered as a top-level command by Task 4's `cli.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_info_command.py`:

```python
from __future__ import annotations

import json
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from devtemplate.commands.info import info

app = typer.Typer()
app.command("info")(info)


@app.command("noop")
def _noop() -> None:
    pass


runner = CliRunner()


def _write_devcontainer_json(tmp_path, config: dict) -> None:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir(exist_ok=True)
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(config))


def test_info_refuses_when_devcontainer_json_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 1
    assert "dvt init" in result.output


def test_info_shows_untracked_features_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path,
        {
            "name": "my-project",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        },
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "my-project" in result.output
    assert "ghcr.io/jesserobertson/base-ubuntu:latest" in result.output
    assert "ghcr.io/jesserobertson/devcontainers/fastapi:latest" in result.output
    assert "untracked" in result.output


def test_info_shows_tracked_feature_names_from_sidecar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    (tmp_path / ".devcontainer" / "dvt-features.json").write_text(
        json.dumps(
            {
                "init": {},
                "applied": [
                    {"name": "fastapi", "overlay": {}},
                    {"name": "agent", "overlay": {}},
                ],
            }
        )
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "fastapi" in result.output
    assert "agent" in result.output
    assert "untracked" not in result.output


def test_info_notes_when_no_runtime_reachable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "no container runtime reachable" in result.output.lower()


def test_info_calls_get_client_without_podman_auto_start_or_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    captured = {}

    def fake_get_client(runtime, **kwargs):
        captured["kwargs"] = kwargs
        return info_module.Err(RuntimeError("no runtime"))

    monkeypatch.setattr(info_module, "get_client", fake_get_client)

    runner.invoke(app, ["info"])

    assert captured["kwargs"] == {
        "podman_machine_auto_init": False,
        "podman_machine_auto_start": False,
    }


def test_info_reports_no_workspace_running_when_zero_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module, "find_workspace_containers_by_folder", lambda client, folder: []
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "dvt up" in result.output


def test_info_shows_live_status_for_single_matching_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    fake_container = MagicMock(status="running", labels={"dvt.workspace": "my-project"})
    fake_container.name = "dvt-my-project"
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module,
        "find_workspace_containers_by_folder",
        lambda client, folder: [fake_container],
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "my-project" in result.output
    assert "running" in result.output
    assert "dvt-my-project" in result.output


def test_info_lists_all_matches_when_multiple_workspaces_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    fake_containers = [
        MagicMock(labels={"dvt.workspace": "bar"}),
        MagicMock(labels={"dvt.workspace": "foo"}),
    ]
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module,
        "find_workspace_containers_by_folder",
        lambda client, folder: fake_containers,
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "bar" in result.output
    assert "foo" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_info_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.commands.info'`

- [ ] **Step 3: Implement `commands/info.py`**

Create `src/devtemplate/commands/info.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from devtemplate.config import load_settings
from devtemplate.container import find_workspace_containers_by_folder
from devtemplate.runtime import get_client
from devtemplate.sidecar import load_sidecar

console = Console()


def info() -> None:
    """Show the current folder's devcontainer setup and any live workspace tied to it."""
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    if not target.exists():
        console.print(
            f"[red]{escape(str(target))} not found.[/red] Run 'dvt init' first."
        )
        raise typer.Exit(code=1)

    try:
        config = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        console.print(
            f"[red]{escape(str(target))} is not strict JSON "
            "(comments/trailing commas are not supported).[/red]"
        )
        raise typer.Exit(code=1) from exc

    console.print(f"Project:  {config.get('name', '?')}  ({Path.cwd()})")
    console.print(f"Image:    {config.get('image', '?')}")

    sidecar_result = load_sidecar(devcontainer_dir)
    applied = sidecar_result.unwrap()["applied"] if sidecar_result.is_ok() else []
    if applied:
        console.print(f"Features: {', '.join(entry['name'] for entry in applied)}")
    else:
        feature_refs = list(config.get("features", {}).keys())
        if feature_refs:
            console.print(f"Features: {', '.join(feature_refs)} (untracked)")

    console.print()

    settings_result = load_settings()
    if settings_result.is_err():
        console.print(f"[yellow]{escape(str(settings_result.unwrap_err()))}[/yellow]")
        return
    settings = settings_result.unwrap()

    client_result = get_client(
        settings.runtime,
        podman_machine_auto_init=False,
        podman_machine_auto_start=False,
    )
    if client_result.is_err():
        console.print(
            "[dim]No container runtime reachable - showing local config only.[/dim]"
        )
        return
    handle = client_result.unwrap()

    containers = find_workspace_containers_by_folder(handle.client, Path.cwd())
    if not containers:
        console.print(
            "No workspace running for this project. Run 'dvt up' to start one."
        )
    elif len(containers) == 1:
        container = containers[0]
        name = container.labels.get("dvt.workspace", "?")
        console.print(
            f"Workspace: {name} - {container.status} (container {container.name})"
        )
    else:
        names = sorted(c.labels.get("dvt.workspace", "?") for c in containers)
        console.print(f"Workspaces matching this folder: {', '.join(names)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_info_command.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/info.py tests/test_info_command.py
git commit -m "feat(dvt): add dvt info, showing local config plus live workspace status"
```

---

### Task 4: Wire up `cli.py` — optional names + register `info`

**Files:**
- Modify: `src/devtemplate/cli.py`
- Modify: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `resolve_for_up`/`resolve_existing` from `devtemplate.workspace_lookup` (Task 2),
  `info` from `devtemplate.commands.info` (Task 3).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_up_infers_name_from_the_single_matching_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime, **kwargs: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_for_up",
        lambda client, name, cwd: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path: (
            captured.update(name=name) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up"])

    assert result.exit_code == 0, result.output
    assert captured["name"] == "reused-name"
    assert "reused-name" in result.output


def test_up_reports_clean_error_when_multiple_workspaces_match(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime, **kwargs: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_for_up",
        lambda client, name, cwd: cli_module.Err(
            ValueError("Multiple workspaces match this folder: bar, foo.")
        ),
    )

    result = runner.invoke(cli_module.app, ["up"])

    assert result.exit_code == 1
    assert "bar" in result.output
    assert "foo" in result.output


def test_ssh_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "exec_interactive",
        lambda cli_binary, client, name: (
            captured.update(name=name) or cli_module.Ok(0)
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_ssh_reports_clean_error_when_no_workspace_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Err(
            ValueError("No workspace found for this folder.")
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh"])

    assert result.exit_code == 1
    assert "No workspace found" in result.output


def test_stop_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"stop": lambda self: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "find_workspace_container",
        lambda client, name: (captured.update(name=name) or fake_container),
    )

    result = runner.invoke(cli_module.app, ["stop"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_delete_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "find_workspace_container",
        lambda client, name: (captured.update(name=name) or fake_container),
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_info_is_registered_as_a_top_level_command():
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_cli.py -v`
Expected: FAIL — the new tests fail (`resolve_for_up`/`resolve_existing` not yet attributes of
`cli_module`, `info` not a registered command); all pre-existing tests in the file still PASS
unchanged (confirming nothing is broken yet, only new functionality is missing).

- [ ] **Step 3: Wire up `cli.py`**

Add to the import block at the top of `src/devtemplate/cli.py` (alongside the existing
`from devtemplate.commands import feature` / `from devtemplate.commands.init import init as
init_command` lines):

```python
from devtemplate.commands.info import info as info_command
from devtemplate.workspace_lookup import resolve_existing, resolve_for_up
```

Register the new command alongside the existing `app.command("init")(init_command)` line:

```python
app.command("info")(info_command)
```

Replace the `up` command's signature and body:

```python
@app.command()
def up(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name for the new workspace (default: inferred from the current folder).",
    ),
) -> None:
    """Build and run a workspace from ./.devcontainer/devcontainer.json."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
    resolved_name = unwrap_or_exit(
        resolve_for_up(handle.client, name, Path.cwd()), console
    )
    unwrap_or_exit(up_workspace(handle, settings, resolved_name, Path.cwd()), console)
    console.print(
        f"[green]Workspace '{resolved_name}' is up.[/green] "
        f"Connect with: dvt ssh {resolved_name} "
        f"(plain 'ssh {resolved_name}' also works, via the ~/.ssh/config entry dvt just wrote)"
    )
```

Replace the `ssh` command's signature and body:

```python
@app.command()
def ssh(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to connect to (default: inferred from the current folder).",
    ),
    stdio: bool = typer.Option(  # noqa: B008
        False,
        "--stdio",
        help="Non-interactive pipe mode for ProxyCommand use.",
        hidden=True,
    ),
) -> None:
    """SSH into a running workspace (or, with --stdio, pipe stdio for ProxyCommand)."""
    errors = Console(stderr=True) if stdio else console
    settings = unwrap_or_exit(load_settings(), errors)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        errors,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "ssh"), errors
    )
    result = (
        stdio_proxy(handle.cli_binary, handle.client, resolved_name)
        if stdio
        else exec_interactive(handle.cli_binary, handle.client, resolved_name)
    )
    exit_code = unwrap_or_exit(result, errors)
    raise typer.Exit(code=exit_code)
```

Replace the `stop` command's signature and body:

```python
@app.command()
def stop(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to stop (default: inferred from the current folder).",
    ),
) -> None:
    """Stop a running workspace."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "stop"), console
    )
    container = _find_or_exit(handle.client, resolved_name)
    try:
        container.stop()
    except Exception as exc:
        console.print(
            f"[red]Failed to stop '{escape(resolved_name)}': {escape(str(exc))}[/red]"
        )
        raise typer.Exit(code=1) from exc
    console.print(f"Stopped '{resolved_name}'.")
```

Replace the `delete` command's signature and body:

```python
@app.command()
def delete(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to delete (default: inferred from the current folder).",
    ),
) -> None:
    """Delete a workspace's container (the built image is left cached)."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "delete"), console
    )
    container = _find_or_exit(handle.client, resolved_name)
    try:
        container.remove(force=True)
    except Exception as exc:
        console.print(
            f"[red]Failed to delete '{escape(resolved_name)}': {escape(str(exc))}[/red]"
        )
        raise typer.Exit(code=1) from exc
    unwrap_or_exit(
        remove_ssh_config_entry(resolved_name, Path.home() / ".ssh" / "config"), console
    )
    console.print(f"Deleted '{resolved_name}'.")
```

`_find_or_exit`, `main()`, and every other part of the file are unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_cli.py tests/test_cli_help.py -v`
Expected: PASS (every test in both files — the new omitted-name tests, `test_info_is_registered`,
and every pre-existing explicit-name test, unmodified)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/cli.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(dvt): make up/ssh/stop/delete's name optional, infer from cwd

Registers dvt info alongside them. Omitted name resolves via the
devcontainer.local_folder container label; explicit-name behavior is
unchanged.
EOF
)"
```

---

### Task 5: Docs + final verification

**Files:**
- Modify: `docs/content/commands.md`
- Modify: `docs/content/quickstart.md`
- Modify: `README.md`

**Interfaces:** None — docs only.

- [ ] **Step 1: Add `dvt info` and the optional-name behavior to `commands.md`**

In `docs/content/commands.md`, add a new section directly after the existing `## `dvt feature`
### `dvt feature remove <name>`` block and before `## Workspace lifecycle`:

```markdown
## `dvt info`

Shows the current folder's devcontainer setup: the project name and base image from
`devcontainer.json`, and its applied features — friendly names from `.devcontainer/dvt-features.json`
if that sidecar exists and has entries, otherwise the raw OCI Feature refs from
`devcontainer.json`'s own `features` map (marked `(untracked)`). Refuses ("run `dvt init`
first") if `.devcontainer/devcontainer.json` doesn't exist. Takes no arguments — always
operates on the current directory, like `dvt feature`.

Then, best-effort: if a container runtime is reachable, reports any live workspace tied to
this folder (via the same `devcontainer.local_folder` label `up`/`ssh`/`stop`/`delete` use to
infer a name below) — its name, running/stopped status, and container name. No runtime
reachable, or none found, is reported plainly rather than as an error; `dvt info` never waits
for a stopped Podman machine to start (unlike `up`/`ssh`/`stop`/`delete`) just to check status.
```

Then, in the `## Workspace lifecycle` section, immediately after its opening paragraph (the one
starting "`dvt up <name>` builds an image..."), add:

```markdown
`<name>` is optional on `up`/`ssh`/`stop`/`delete` — when omitted, dvt looks for a workspace
already tied to the current folder (via its `devcontainer.local_folder` container label, not
just the folder's own name, so it still finds a workspace created under a different name).
Exactly one match reuses it; for `up`, no match falls back to the folder's own directory name
to create a fresh workspace; for `ssh`/`stop`/`delete`, no match refuses (nothing to act on);
more than one match always refuses, listing every candidate name and asking for an explicit
one.
```

- [ ] **Step 2: Mention `dvt info` and omitted names in `quickstart.md`**

Replace the entire contents of `docs/content/quickstart.md` (steps 6 onward are renumbered and
step 6 "Check on it" is new; steps 1-5 are unchanged) with:

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
project directory. It's optional too: omit it and `up` uses the current directory's own name
(`my-api` here) the first time, or reuses whatever workspace is already tied to this folder on
a later run:

```bash
dvt up
```

`dvt` pulls each added Feature, builds a multi-stage image from it, runs the container,
and runs `postCreateCommand` (then `postStartCommand`, if a feature sets one).

## 6. Check on it

```bash
dvt info
```

Shows the project's image and applied features, plus — since it's running — its live status:
name, running/stopped state, and container name. `<name>` isn't needed here either; `info`
always operates on the current directory.

## 7. Connect

```bash
dvt ssh
```

Same story: no name needed from inside the project directory. (`dvt ssh my-api` still works
too, from anywhere.)

## 8. Remove a feature

```bash
dvt feature remove agent
```

Restores the fields `agent` touched, leaving everything else in `devcontainer.json` —
including `fastapi`'s own contribution and any manual edits — untouched.

## 9. Stop or remove the workspace

```bash
dvt stop
dvt delete
```
```

- [ ] **Step 3: Update `README.md`'s Usage section**

In `README.md`, in the `## Usage` section, add after the existing `dvt ssh my-project` line:

```
    dvt info                        # from inside my-project - no name needed
```

- [ ] **Step 4: Full verification**

Run: `pixi run test unit`
Expected: PASS (every unit test in the suite)

Run: `pixi run quality check`
Expected: PASS (mypy, ruff lint, ruff format — all "Pass")

- [ ] **Step 5: Commit**

```bash
git add docs/content/commands.md docs/content/quickstart.md README.md
git commit -m "docs(dvt): document dvt info and optional up/ssh/stop/delete names"
```
