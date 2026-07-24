# dvt Integration Tests + Full Docs Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, real (non-mocked) `devpod` lifecycle integration test to `dvt`, and replace its minimal docs skeleton with a full reference site.

**Architecture:** Two independent tasks. Task 1 adds `dvt/tests/integration/test_devpod_lifecycle.py`, exercising `dvt up`/`stop`/`delete` for real against a minimal self-contained fixture project — skipped cleanly when `devpod` isn't installed, never run in CI (already-decided: real containers in CI are slow/flaky). Task 2 adds four new mkdocs content pages plus a `mkdocs.yml` nav update, verified by `mkdocs build --strict`.

**Tech Stack:** pytest (existing `integration` marker), Typer's `CliRunner`, `devpod` + Docker (Task 1 only, opt-in); MkDocs Material + mkdocstrings (already configured, Task 2 only).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-25-dvt-integration-tests-and-docs-design.md`.
- Task 1's fixture `devcontainer.json` uses a plain public base image (`mcr.microsoft.com/devcontainers/base:ubuntu`), no custom features, no `postCreateCommand` — deliberately NOT a template synced from GitHub. This isolates the test from a separate, already-flagged bug in this repo's `features/*/install.sh` scripts (unrelated to `dvt`, belongs to a different plan) where the "copy a default `pixi.toml`" logic runs at image-build time before the real project is bind-mounted at `/workspace`, so `postCreateCommand: pixi install` fails for any project with no `pixi.toml` of its own.
- Task 1 does NOT test `dvt ssh`. A real `devpod ssh` permission error was observed during manual testing that reproduced identically with bare `devpod ssh` (no `dvt` code involved), confirming it isn't something a `dvt`-level test can meaningfully assert on. `ssh`'s command construction is already covered by `dvt/tests/test_cli.py`'s existing mocked tests.
- Task 1 must never touch `devpod`'s provider configuration (host-level state) and must always clean up (via `try`/`finally`) regardless of test outcome.
- Task 1 is marked `@pytest.mark.integration` (already registered in `dvt/pyproject.toml`'s `[tool.pytest.ini_options] markers`) and additionally `@pytest.mark.skipif(shutil.which("devpod") is None, ...)`. It must NOT be added to `.github/workflows/dvt-ci.yml` or to `scripts/test.py`'s `all`/`fast` commands' CI usage — it only ever runs via `pixi run test integration`, invoked manually.
- Task 2's four new pages go in `dvt/docs/content/`, matching the existing `index.md`/`api.md` location. `mkdocs.yml`'s `docs_dir: content` and `mkdocstrings` `paths: ["../src"]` are already correctly configured — do not change them.
- All commands assume the working directory is `dvt/` unless stated otherwise.

---

### Task 1: Real devpod lifecycle integration test

**Files:**
- Create: `dvt/tests/integration/test_devpod_lifecycle.py`

**Interfaces:**
- Consumes: `devtemplate.cli.app` (already implemented — the `up`/`stop`/`delete` Typer commands from `dvt/src/devtemplate/cli.py`). No production code changes in this task.
- Produces: nothing consumed by later tasks — this and Task 2 are independent.

- [ ] **Step 1: Create the integration test directory and file**

Create `dvt/tests/integration/test_devpod_lifecycle.py`:

```python
"""Real devpod lifecycle integration test.

Opt-in only — run with `pixi run test integration`, never part of `pixi run test all`,
`pixi run pytest`, or CI. Requires `devpod` on PATH and a working container runtime
(skips cleanly, not a failure, if devpod isn't installed).

Deliberately does NOT test `ssh`: a real `devpod ssh` permission error was observed during
manual testing that reproduced identically calling bare `devpod ssh` directly, with no dvt
code involved at all — confirming it isn't something a dvt-level test can meaningfully
assert on. ssh's command construction is already covered by dvt/tests/test_cli.py's mocked
tests.

Deliberately uses a minimal, self-authored devcontainer.json (a plain public base image, no
custom features, no postCreateCommand) rather than a template synced from GitHub. This
isolates what this test verifies — does dvt correctly drive a real devpod through a
container lifecycle — from a separate, already-flagged bug in this repo's own
features/*/install.sh scripts (unrelated to dvt): their "copy a default pixi.toml if none
exists" logic runs at image-build time, before the real project directory is bind-mounted
at /workspace at container start, so the default gets shadowed and postCreateCommand: pixi
install fails for any project with no pixi.toml of its own. That bug belongs to a different,
already-completed plan.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()

pytestmark = pytest.mark.integration

devpod_missing = shutil.which("devpod") is None


@pytest.fixture
def real_project(tmp_path: Path) -> Path:
    """A minimal, self-contained devcontainer.json project — no dvt template involved."""
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "dvt-integration-test",
                "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
            }
        )
    )
    return tmp_path


@pytest.mark.skipif(devpod_missing, reason="devpod not installed")
def test_up_stop_delete_lifecycle(real_project: Path) -> None:
    """Real devpod up -> stop -> delete against a real container runtime, no mocking."""
    workspace_id = f"dvt-integration-test-{uuid.uuid4().hex[:8]}"

    try:
        up_result = runner.invoke(
            app,
            ["up", str(real_project), "--id", workspace_id, "--ide", "none"],
        )
        assert up_result.exit_code == 0, up_result.output

        stop_result = runner.invoke(app, ["stop", workspace_id])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_id])
```

Note on `--id`/`--ide`: these are `devpod up`'s own flags, forwarded through `dvt up`'s
`extra_args` parameter (`dvt up <path> --id <x> --ide <y>` → `devpod up <path> --id <x> --ide
<y>`, no `dvt`-side special-casing needed). `--id` gives the workspace a deterministic,
collision-resistant name (a random path-derived default would collide across repeated runs);
`--ide none` prevents `devpod up` from attempting to launch a GUI editor (observed during
manual testing: it defaulted to trying to open Zed) during an automated test run.

- [ ] **Step 2: Run it to confirm it's collected and either passes or skips correctly**

Run: `cd dvt && pixi run pytest tests/integration/ -v -m integration`

Expected, if `devpod` is NOT installed on the machine running this:
```
tests/integration/test_devpod_lifecycle.py::test_up_stop_delete_lifecycle SKIPPED (devpod not installed)
```

Expected, if `devpod` IS installed with a working container runtime already configured as
its default provider:
```
tests/integration/test_devpod_lifecycle.py::test_up_stop_delete_lifecycle PASSED
```
This will take a minute or more (real image pull/build). If it fails with a provider
connectivity error (e.g. a misconfigured default provider), that's a real, informative
failure for the person running it to resolve locally — do not change `devpod`'s provider
configuration to work around it, per the Global Constraints.

- [ ] **Step 3: Confirm the marker filtering excludes this test from the default runs**

Run: `cd dvt && pixi run pytest -v` (no marker filter — this is what CI and `pixi run test
all` ultimately drive)
Expected: this new test does NOT appear in the output at all — the existing
`pytest_collection_modifyitems` hook in `dvt/tests/conftest.py` only auto-applies the `unit`
marker to tests with none of `integration`/`slow`/`network`; this test already has
`pytestmark = pytest.mark.integration` set explicitly, so it's correctly excluded from `-m
unit` runs. This step is a sanity check, not something requiring new code — if it fails,
something is wrong with how `pytestmark` was applied above, not with `conftest.py`.

Run: `cd dvt && pixi run mypy src`
Expected: `Success: no issues found in 12 source files` — this task adds no `src/` changes,
so this must already be true; confirms nothing regressed.

- [ ] **Step 4: Commit**

```bash
git add dvt/tests/integration/test_devpod_lifecycle.py
git commit -m "test: add opt-in integration test for the real devpod up/stop/delete lifecycle"
```

---

### Task 2: Full docs reference content

**Files:**
- Create: `dvt/docs/content/installation.md`
- Create: `dvt/docs/content/quickstart.md`
- Create: `dvt/docs/content/commands.md`
- Create: `dvt/docs/content/concepts.md`
- Modify: `dvt/docs/mkdocs.yml`

**Interfaces:**
- Consumes: nothing from Task 1 — independent.
- Produces: nothing consumed by later tasks — this is the plan's last task.

- [ ] **Step 1: Create the four content pages**

Create `dvt/docs/content/installation.md`:

```markdown
# Installation

## Requirements

- Python 3.12 or later
- [`pipx`](https://pipx.pypa.io/) (recommended) or `pip`
- [DevPod](https://devpod.sh) and a container runtime (Docker Desktop, Podman, etc.) — only
  needed for the `up`/`ssh`/`stop`/`delete` commands. `template`/`project` commands work
  without either.

## Install

```bash
pipx install ./dvt
```

`logerr`, one of `dvt`'s dependencies, isn't published to PyPI yet — it's pinned as a git
dependency, so installation requires network access to `github.com/jesserobertson/logerr` in
addition to PyPI.

## Verify

```bash
dvt --help
```

## Upgrading

Since `dvt` isn't published to PyPI, reinstall from an updated checkout to upgrade:

```bash
git -C /path/to/devcontainers pull
pipx install --force /path/to/devcontainers/dvt
```
```

Create `dvt/docs/content/quickstart.md`:

```markdown
# Quickstart

This walkthrough scaffolds a new project from the `fastapi` template, layers on the `agent`
feature, and starts it in a real DevPod-managed container.

## 1. Sync templates

Templates are fetched from this repo's `templates/` directory on GitHub:

```bash
dvt template sync
```

```
Synced 12 templates: agent, cli, fastapi, huggingface, jax, marimo, mojo, ollama,
py-devtools, pytorch, rapids, transformers
```

## 2. See what's available

```bash
dvt template list
```

## 3. Scaffold a project

```bash
dvt project init --template fastapi ./my-api
```

This writes `./my-api/.devcontainer/devcontainer.json`, with `name` set to the target
directory's own name (`my-api`), not the template's.

## 4. Layer on another feature

```bash
cd my-api
dvt project add-feature agent
```

This merges the `agent` feature's requirements (its own `features` entry, `runArgs`,
`postStartCommand`, etc.) into the existing `devcontainer.json` — see
[Concepts](concepts.md) for exactly how the merge works. If merging would produce an invalid
`devcontainer.json`, `add-feature` refuses to write and leaves the file untouched.

## 5. Start the container

```bash
dvt up .
```

DevPod builds the image (or reuses a cached one), applies the features, and runs
`postCreateCommand`.

## 6. Connect

```bash
dvt ssh my-api
```

## 7. Stop or remove it

```bash
dvt stop my-api
dvt delete my-api
```
```

Create `dvt/docs/content/commands.md`:

```markdown
# Command Reference

## `dvt template`

### `dvt template sync`

Fetches every template from `templates/` in the configured GitHub repository (default
`jesserobertson/devcontainers`, branch `main` — override with the `DVT_GITHUB_REPO` /
`DVT_GITHUB_BRANCH` environment variables) into the local cache. Prunes any previously-synced
template that's been removed upstream; never touches a template directory you've added by
hand.

### `dvt template list`

Lists cached templates with their base image and declared feature.

### `dvt template show <name>`

Prints a cached template's `devcontainer.json`.

## `dvt project`

### `dvt project init <path> --template <name>`

Scaffolds `<path>/.devcontainer/devcontainer.json` from a cached template. Auto-syncs first
if the cache is empty, or always if `--refresh` is passed. The scaffolded file's `name` field
is set to `<path>`'s own directory name, not the template's. Refuses (exit 1, nothing
written) if `<path>/.devcontainer/devcontainer.json` already exists, or if the template
itself doesn't pass schema validation.

### `dvt project add-feature <name>`

Merges another feature's template into `./.devcontainer/devcontainer.json` (always
cwd-relative). See [Concepts](concepts.md) for the merge semantics. Refuses to write (file
left byte-for-byte unchanged) if:

- `.devcontainer/devcontainer.json` doesn't exist
- it exists but isn't strict JSON (comments/trailing commas aren't supported)
- the feature name isn't cached (run `dvt template sync` first)
- the merge result would fail validation against the official devcontainer.json schema

## Lifecycle passthroughs

`dvt up`, `dvt ssh`, `dvt stop`, `dvt delete` all forward directly to the equivalent `devpod`
command, passing through any extra arguments and the real exit code unmodified — `dvt ssh
my-project -- pytest` returns `pytest`'s actual exit code, not something `dvt` interprets.
These require `devpod` on `PATH` and a working container runtime; if `devpod` can't be found,
`dvt` reports a clean error rather than a raw traceback (this failure mode is NOT retried —
unlike `template sync`'s GitHub calls, a devpod exit code is meaningful output to forward,
not a transient error).

```bash
dvt up <path-or-workspace-name> [extra devpod args]
dvt ssh <workspace-name> [-- command]
dvt stop <workspace-name>
dvt delete <workspace-name>
```
```

Create `dvt/docs/content/concepts.md`:

```markdown
# Concepts

## The merge algorithm

`dvt project add-feature` layers a new feature's template onto an existing project's
`devcontainer.json` using a field-typed merge (ported from
[`dev`](https://github.com/squirrelsoft-dev/dev)'s Rust implementation), not a generic deep
merge:

| Field(s) | Rule |
|---|---|
| `name`, `image`, `remoteUser`, `waitFor`, `shutdownAction` | scalar — the new feature's value overrides |
| `features` | union by key — new feature's entry wins on collision |
| `mounts`, `forwardPorts` | concatenate, deduplicated |
| `runArgs` | concatenate **without** dedup — repeated flags (e.g. multiple `--env-file`) are legitimate |
| `remoteEnv`, `containerEnv` | map merge — new feature's keys win on collision |
| `postCreateCommand`, `postStartCommand`, `postAttachCommand`, `onCreateCommand`, `updateContentCommand`, `initializeCommand` | union only if both sides use the named-command-object form; otherwise the new feature's value replaces outright |
| anything else | new feature's value wins |

Fields the project already has that the new feature's template doesn't mention are left
exactly as they are.

## Why `name`/`workspaceFolder`/`workspaceMount` are stripped first

Before merging, `add-feature` removes `name`, `workspaceFolder`, and `workspaceMount` from
the incoming feature template. Every template in this repo sets its own `name` to its own
feature name — `templates/agent/devcontainer.json` literally has `"name": "agent"`. Applying
the merge rule above unfiltered would silently rename your project to whatever feature you
just added. `workspaceFolder`/`workspaceMount` are identical across every template anyway, so
stripping them costs nothing.

## Schema validation on write

Before writing, both `add-feature` and `project init` validate the result against a vendored
copy of the official [devcontainer.json base
schema](https://github.com/devcontainers/spec/blob/main/schemas/devContainer.base.schema.json).
If validation fails, nothing is written — the target file is left exactly as it was. This
matters because `DVT_GITHUB_REPO` is user-overridable: a malicious or just-broken fork's
templates get caught before they ever touch your project.
```

- [ ] **Step 2: Update the nav**

In `dvt/docs/mkdocs.yml`, replace:

```yaml
nav:
  - Home: index.md
  - API Reference: api.md
```

with:

```yaml
nav:
  - Home: index.md
  - Installation: installation.md
  - Quickstart: quickstart.md
  - Commands: commands.md
  - Concepts: concepts.md
  - API Reference: api.md
```

Everything else in `dvt/docs/mkdocs.yml` (`docs_dir`, `mkdocstrings` `paths`, theme, `watch`)
stays exactly as it is.

- [ ] **Step 3: Build and verify**

Run: `cd dvt && pixi run docs build --strict`
Expected: builds cleanly, exit code 0, no warnings (`--strict` turns warnings — e.g. a broken
internal link between the new pages — into build failures, so a clean exit here is the real
verification that the new pages and nav are correctly wired together).

- [ ] **Step 4: Commit**

```bash
git add dvt/docs/content/installation.md dvt/docs/content/quickstart.md dvt/docs/content/commands.md dvt/docs/content/concepts.md dvt/docs/mkdocs.yml
git commit -m "docs: add full installation/quickstart/commands/concepts reference"
```

---

## Self-Review Notes

- **Spec coverage:** Part 1 (integration test: minimal fixture, no ssh, no CI, no provider
  mutation, always-cleanup, deterministic workspace id) ✓ Task 1. Part 2 (four content pages,
  nav update) ✓ Task 2.
- **Placeholder scan:** no TBD/TODO; every step shows complete file content.
- **Type consistency:** Task 1 uses `devtemplate.cli.app` exactly as already defined in
  `dvt/src/devtemplate/cli.py` (no new interfaces introduced). Task 2 introduces no code,
  only content + one YAML edit.
- **Independence:** Tasks 1 and 2 touch entirely disjoint files and can be implemented (and
  reviewed) in either order.
