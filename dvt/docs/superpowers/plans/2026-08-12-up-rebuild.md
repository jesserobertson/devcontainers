# `dvt up --rebuild` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dvt up` detects when an existing workspace's container was built from a different `devcontainer.json` than what's on disk, refuses rather than silently resuming it, and a new `--rebuild` flag tears the workspace down and rebuilds it from scratch.

**Architecture:** Reuse the `devcontainer.metadata` container label `compute_labels` already writes (the full resolved config, base64-JSON) as the sole source of truth for "what was this container built from." A new pair of pure functions in `container.py` (`read_stored_config`, `config_has_drifted`) decode and compare it against a fresh parse of the on-disk file. `up_workspace` in `workspace.py` uses these on its existing-container branch to either resume (unchanged config), refuse (changed config, no `--rebuild`), or tear down and fall through into its existing fresh-build path (`--rebuild`, with `build_image` given new `nocache`/`pull` flags to force real freshness). `cli.py` threads a new `--rebuild` option through.

**Tech Stack:** Python 3.12+, `logerr` (`Result`/`Ok`/`Err`/`wrap_result`), `docker-py` (`DockerClient`/`Container`), `typer`, `pytest` with `unittest.mock.MagicMock` and per-function `monkeypatch.setattr`.

## Global Constraints

- Every function that can raise gets wrapped with `@wrap_result` from `logerr.utilities`, per this codebase's existing convention — never a bare `try/except` returning a hand-rolled `Result`.
- Docstrings explain *why*, not just *what* — match the multi-sentence style already used throughout `container.py`/`workspace.py` (e.g. `compute_labels`, `_resume_existing`).
- Tests follow each file's existing per-function-monkeypatch style (`tests/test_container.py`, `tests/test_build.py`, `tests/test_workspace.py`, `tests/test_cli.py`) — no new test infrastructure/fixtures beyond what's specified below.
- Run `pytest` (whole suite) at the end of each task, not just the new/changed test file, since Task 3 modifies shared fixtures/constants other tests in the same file depend on.

---

## Task 1: Drift-detection primitives in `container.py`

**Files:**
- Modify: `src/devtemplate/container.py:6` (import), `src/devtemplate/container.py:78-90` (`compute_labels`)
- Test: `tests/test_container.py`

**Interfaces:**
- Produces: `read_stored_config(container: Container) -> Result[dict[str, Any], Exception]`, `config_has_drifted(container: Container, config: dict[str, Any]) -> bool` — both consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_container.py` (needs `import base64` at the top alongside the existing imports, and `read_stored_config`, `config_has_drifted` added to the `from devtemplate.container import (...)` block):

```python
def test_read_stored_config_round_trips_compute_labels(tmp_path):
    config = {"name": "x", "image": "base:latest"}
    labels = compute_labels(config, "x", tmp_path, tmp_path / "devcontainer.json")
    fake_container = MagicMock()
    fake_container.labels = labels

    result = read_stored_config(fake_container)

    assert result.is_ok()
    assert result.unwrap() == config


def test_read_stored_config_errs_on_missing_label():
    fake_container = MagicMock()
    fake_container.labels = {}

    result = read_stored_config(fake_container)

    assert result.is_err()


def test_read_stored_config_errs_on_invalid_json_label():
    fake_container = MagicMock()
    fake_container.labels = {
        "devcontainer.metadata": base64.b64encode(b"not json").decode()
    }

    result = read_stored_config(fake_container)

    assert result.is_err()


def test_config_has_drifted_false_when_config_matches(tmp_path):
    config = {"name": "x", "image": "base:latest"}
    labels = compute_labels(config, "x", tmp_path, tmp_path / "devcontainer.json")
    fake_container = MagicMock()
    fake_container.labels = labels

    assert config_has_drifted(fake_container, config) is False


def test_config_has_drifted_true_when_config_changed(tmp_path):
    original = {"name": "x", "image": "base:latest"}
    labels = compute_labels(original, "x", tmp_path, tmp_path / "devcontainer.json")
    fake_container = MagicMock()
    fake_container.labels = labels
    changed = {**original, "postCreateCommand": "pixi install"}

    assert config_has_drifted(fake_container, changed) is True


def test_config_has_drifted_true_when_stored_config_unreadable():
    fake_container = MagicMock()
    fake_container.labels = {}

    assert config_has_drifted(fake_container, {"name": "x"}) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_container.py -k "read_stored_config or config_has_drifted" -v`
Expected: FAIL with `ImportError`/`AttributeError` (`read_stored_config`/`config_has_drifted` don't exist yet).

- [ ] **Step 3: Implement `_encode_metadata`, `read_stored_config`, `config_has_drifted`**

In `src/devtemplate/container.py`, change the import line (line 6) from:

```python
from typing import Any
```

to:

```python
from typing import Any, cast
```

Then replace `compute_labels` (lines 78-90) with:

```python
def _encode_metadata(config: dict[str, Any]) -> str:
    """base64(json.dumps(config)) - the exact devcontainer.metadata label value.
    Factored out of compute_labels so read_stored_config's decode side and this
    encode side stay obviously in sync."""
    return base64.b64encode(json.dumps(config).encode()).decode()


def compute_labels(
    config: dict[str, Any], name: str, project_path: Path, config_file: Path
) -> dict[str, str]:
    """The label contract other devcontainer-aware tooling (VS Code's Dev
    Containers extension, @devcontainers/cli, devpod) uses to recognize and
    introspect a container dvt built."""
    return {
        "devcontainer.metadata": _encode_metadata(config),
        "devcontainer.local_folder": str(project_path.resolve()),
        "devcontainer.config_file": str(config_file.resolve()),
        "dvt.workspace": name,
    }


@wrap_result
def read_stored_config(container: Container) -> dict[str, Any]:
    """Decode a container's devcontainer.metadata label back into the dict it
    was built from. Errs if the label is missing or isn't valid base64/JSON -
    every container dvt itself builds always carries a well-formed one via
    compute_labels, so a failure here means a foreign or corrupted container."""
    encoded = container.labels.get("devcontainer.metadata")
    if encoded is None:
        raise ValueError("container has no devcontainer.metadata label")
    return cast(dict[str, Any], json.loads(base64.b64decode(encoded).decode()))


def config_has_drifted(container: Container, config: dict[str, Any]) -> bool:
    """True if container's stored config differs from config (the current
    on-disk devcontainer.json, already parsed). Dict equality, not label-string
    equality - JSON key order isn't meaningful. An unreadable stored config
    counts as drifted: better to ask for --rebuild than silently resume a
    container whose provenance can't be verified."""
    return (
        read_stored_config(container).map(lambda stored: stored != config).unwrap_or(True)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_container.py -v`
Expected: PASS (all tests in the file, including the pre-existing `test_compute_labels_encodes_metadata`).

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/container.py tests/test_container.py
git commit -m "feat(dvt): add container config drift detection primitives"
```

---

## Task 2: `build_image` gains `nocache`/`pull` for forced-fresh builds

**Files:**
- Modify: `src/devtemplate/build.py:63-85`
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_image(client, base_image, features, tag, scratch_dir, *, nocache: bool = False, pull: bool = False) -> Result[str, Exception]` — consumed by Task 3.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_build.py`:

```python
def test_build_image_defaults_to_cached_build(tmp_path):
    fake_client = MagicMock()
    fake_client.images.build.return_value = (MagicMock(), iter([]))

    result = build_image(
        fake_client, "base:latest", [], "dvt/x:latest", tmp_path / "scratch"
    )

    assert result.is_ok()
    _, kwargs = fake_client.images.build.call_args
    assert kwargs["nocache"] is False
    assert kwargs["pull"] is False


def test_build_image_forces_fresh_build_when_requested(tmp_path):
    fake_client = MagicMock()
    fake_client.images.build.return_value = (MagicMock(), iter([]))

    result = build_image(
        fake_client,
        "base:latest",
        [],
        "dvt/x:latest",
        tmp_path / "scratch",
        nocache=True,
        pull=True,
    )

    assert result.is_ok()
    _, kwargs = fake_client.images.build.call_args
    assert kwargs["nocache"] is True
    assert kwargs["pull"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_build.py -k forces_fresh_build_or_defaults_to_cached -v`
Expected: FAIL with `TypeError: build_image() got an unexpected keyword argument 'nocache'`.

- [ ] **Step 3: Implement**

In `src/devtemplate/build.py`, replace the `build_image` function (lines 63-85) with:

```python
@wrap_result
def build_image(
    client: DockerClient,
    base_image: str,
    features: list[tuple[str, Path, dict[str, str]]],
    tag: str,
    scratch_dir: Path,
    *,
    nocache: bool = False,
    pull: bool = False,
) -> str:
    """Assemble a build context under scratch_dir (copying each extracted Feature
    directory in), write the generated Dockerfile, and build it. features: list of
    (feature_id, extracted_dir, resolved_options).

    nocache/pull default to False (normal cached build). Set both True to force
    a from-scratch rebuild (used by `dvt up --rebuild`): nocache disables
    Docker's build-layer cache, pull re-fetches the base image even if a local
    copy exists - together they pick up a moved upstream base image tag or a
    stale intermediate layer. Simply deleting the previously built image tag
    would not achieve this, since Docker's build cache is keyed by instruction
    content, not by output tag."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    context_features: list[tuple[str, str, dict[str, str]]] = []
    for index, (feature_id, extracted_dir, options) in enumerate(features):
        context_relative = f"features/{index}-{feature_id}"
        shutil.copytree(extracted_dir, scratch_dir / context_relative)
        context_features.append((feature_id, context_relative, options))

    dockerfile_content = generate_dockerfile(base_image, context_features)
    (scratch_dir / "Dockerfile").write_text(dockerfile_content)

    client.images.build(
        path=str(scratch_dir), tag=tag, rm=True, nocache=nocache, pull=pull
    )
    return tag
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_build.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/build.py tests/test_build.py
git commit -m "feat(dvt): add nocache/pull options to build_image for forced-fresh builds"
```

---

## Task 3: `up_workspace` detects drift and handles `--rebuild`

**Files:**
- Modify: `src/devtemplate/workspace.py:48-127`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `read_stored_config`, `config_has_drifted` from Task 1 (`devtemplate.container`); `build_image(..., nocache=..., pull=...)` from Task 2.
- Produces: `up_workspace(handle, settings, name, project_path, rebuild: bool = False) -> Result[Container, Exception]` — consumed by Task 4.

**Important design note this task resolves:** the pre-existing test
`test_up_workspace_resumes_existing_even_without_devcontainer_json` asserts `_load_config` is
never called on the resume path (a workspace must stay resumable even with no/invalid
`devcontainer.json`). Drift detection needs to read the current config to compare it, which
means `_load_config` *is* now called on the resume path — but its failure (file missing/invalid)
must not block resuming; it just means the drift check is skipped. This preserves the original
invariant's actual guarantee (resuming never depends on `devcontainer.json`'s presence) while
updating the test to match the new, legitimate reason `_load_config` gets called. This is
different from `config_has_drifted`'s own "unreadable *container label*" case (Task 1), which
*does* block resuming (Err) — that failure means the container's own provenance can't be
verified, not that the on-disk file is merely absent.

- [ ] **Step 1: Update the `project` fixture to expose its config as a reusable constant**

In `tests/test_workspace.py`, replace the `project` fixture (lines 15-29) with:

```python
PROJECT_CONFIG = {
    "name": "fastapi",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
    "postCreateCommand": "pixi install",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(PROJECT_CONFIG))
    return tmp_path
```

Add `compute_labels` to the imports at the top of the file:

```python
from devtemplate.container import compute_labels
```

- [ ] **Step 2: Update the two existing resume tests to give `existing` real labels**

Replace `test_up_workspace_starts_existing_stopped_container` (lines 104-121) with:

```python
def test_up_workspace_starts_existing_stopped_container(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.status = "exited"
    existing.labels = compute_labels(
        PROJECT_CONFIG, "fastapi", project, project / ".devcontainer" / "devcontainer.json"
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )
    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_called_once()
```

Replace `test_up_workspace_noop_when_already_running` (lines 123-137) with:

```python
def test_up_workspace_noop_when_already_running(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "running"
    existing.labels = compute_labels(
        PROJECT_CONFIG, "fastapi", project, project / ".devcontainer" / "devcontainer.json"
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )
    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_not_called()
```

- [ ] **Step 3: Update the no-devcontainer.json resume test**

Replace `test_up_workspace_resumes_existing_even_without_devcontainer_json` (lines 140-168) with:

```python
def test_up_workspace_resumes_existing_even_without_devcontainer_json(
    tmp_path, handle, settings, monkeypatch
):
    """devcontainer.json state must never gate resumability. up_workspace now
    reads it on the resume path too (to check for drift), but a missing file
    must not block resuming - the drift check is simply skipped, and the
    existing container is resumed exactly as if devcontainer.json were
    present and unchanged. Here there is no .devcontainer/devcontainer.json
    at all."""
    existing = MagicMock()
    existing.status = "exited"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", tmp_path)

    assert result.is_ok()
    assert result.unwrap() is existing
    existing.start.assert_called_once()
```

(This removes the `_fail_load_config`/monkeypatch block that used to assert `_load_config` is
never called — it legitimately is called now, and errors internally, which the code tolerates.)

- [ ] **Step 4: Write new failing tests for drift refusal and `--rebuild`**

Append to `tests/test_workspace.py`:

```python
def test_up_workspace_refuses_when_config_drifted(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "running"
    existing.labels = compute_labels(
        PROJECT_CONFIG, "fastapi", project, project / ".devcontainer" / "devcontainer.json"
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    (project / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps({**PROJECT_CONFIG, "postCreateCommand": "pixi install --locked"})
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_err()
    assert "postCreateCommand" in str(result.unwrap_err())
    existing.remove.assert_not_called()


def test_up_workspace_refuses_when_stored_config_unreadable(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.labels = {}
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_err()
    assert "couldn't verify" in str(result.unwrap_err())
    existing.remove.assert_not_called()


def test_up_workspace_rebuild_tears_down_and_rebuilds(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.labels = compute_labels(
        PROJECT_CONFIG, "fastapi", project, project / ".devcontainer" / "devcontainer.json"
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    build_calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: build_calls.append(k) or Ok("dvt/fastapi:latest"),
    )
    fake_new_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_new_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    assert result.unwrap() is fake_new_container
    existing.remove.assert_called_once_with(force=True)
    handle.client.images.remove.assert_called_once_with("dvt/fastapi:latest", force=True)
    assert build_calls == [{"nocache": True, "pull": True}]


def test_up_workspace_rebuild_skips_teardown_when_no_existing_container(
    project, handle, settings, monkeypatch
):
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: Ok("dvt/fastapi:latest")
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    assert result.unwrap() is fake_container
    handle.client.images.remove.assert_not_called()


def test_up_workspace_rebuild_proceeds_when_image_removal_fails(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.labels = compute_labels(
        PROJECT_CONFIG, "fastapi", project, project / ".devcontainer" / "devcontainer.json"
    )
    handle.client.images.remove.side_effect = RuntimeError("image in use")
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: Ok("dvt/fastapi:latest")
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    existing.remove.assert_called_once_with(force=True)
```

- [ ] **Step 5: Run tests to verify the new/changed ones fail**

Run: `pytest tests/test_workspace.py -v`
Expected: the drift/rebuild tests FAIL (`TypeError: up_workspace() got an unexpected keyword
argument 'rebuild'`); the three updated resume tests currently pass but will be re-verified in
Step 7.

- [ ] **Step 6: Implement**

In `src/devtemplate/workspace.py`, replace lines 48-127 (`_resume_existing` through the end of
`up_workspace`) with:

```python
@wrap_result
def _resume_existing(existing: Container, name: str) -> Container:
    """Handle the re-`up` case where a container already carries this
    workspace's label: start it if it isn't running, then (re)write its SSH
    config entry. Every fallible operation on `existing` - status access and
    start(), both of which can raise docker-py's APIError - is wrapped, so
    nothing escapes as a bare exception.
    """
    if existing.status != "running":
        existing.start()
    _refresh_ssh_config(name).unwrap()
    return existing


def _config_drift_error(existing: Container, config: dict[str, Any], name: str) -> Exception:
    """Build the Err raised when an existing workspace's container doesn't
    match the current devcontainer.json. Distinguishes "config on disk
    differs from what was built" (lists the changed top-level keys) from
    "can't tell" (the container's own devcontainer.metadata label is
    unreadable) - both point at --rebuild, but the message says why."""
    stored_result = read_stored_config(existing)
    if stored_result.is_err():
        return ValueError(
            f"Workspace {name!r} already exists but dvt couldn't verify its "
            f"config ({stored_result.unwrap_err()}). Run 'dvt up --rebuild' "
            "to rebuild it."
        )
    stored = stored_result.unwrap()
    changed_keys = sorted(
        key
        for key in stored.keys() | config.keys()
        if stored.get(key) != config.get(key)
    )
    return ValueError(
        f"Workspace {name!r} already exists but its devcontainer.json has "
        f"changed since it was built ({', '.join(changed_keys)}). Run "
        "'dvt up --rebuild' to rebuild it, or 'dvt up' again to keep using "
        "the existing container."
    )


@wrap_result
def _rebuild_teardown(client: DockerClient, existing: Container, image_tag: str) -> None:
    """Remove the existing container so the fresh-build path below can run as
    if no workspace existed yet. Only existing.remove() failing is fatal
    (surfaced as Err) - if the old container can't be removed, --rebuild
    can't safely proceed. Dropping the cached image tag afterward is
    best-effort and swallowed on failure: it's purely for `docker images`
    hygiene, since the upcoming build_image(nocache=True, pull=True) call
    overwrites the tag regardless and is what actually forces freshness, not
    this removal.
    """
    existing.remove(force=True)
    try:
        client.images.remove(image_tag, force=True)
    except Exception:
        pass


@wrap_result
def up_workspace(
    handle: RuntimeHandle,
    settings: Settings,
    name: str,
    project_path: Path,
    rebuild: bool = False,
) -> Container:
    """Full `dvt up` sequence: validate -> pull Features -> build -> run ->
    lifecycle commands -> SSH config. Returns the running Container.

    Handles the re-`up` case (a workspace with this name already exists): if
    devcontainer.json is unreadable, or matches what the container was built
    from (compared via its devcontainer.metadata label), resumes it exactly
    as before. If devcontainer.json differs and `rebuild` is False, refuses
    with a message naming the changed keys and pointing at `--rebuild`. If
    `rebuild` is True, tears down the existing container and its cached
    image tag first (regardless of whether config actually drifted -
    `--rebuild` is also the general force-fresh escape hatch for e.g. a moved
    upstream base image tag), then falls through into the same
    build-from-scratch sequence used when no container exists yet, with
    Docker's build cache and base-image reuse both disabled.
    """
    existing = find_workspace_container(handle.client, name)
    config_file = project_path / ".devcontainer" / "devcontainer.json"

    if existing is not None:
        if not rebuild:
            config_result = _load_config(config_file)
            if config_result.is_ok():
                current_config = config_result.unwrap()
                if config_has_drifted(existing, current_config):
                    raise _config_drift_error(existing, current_config, name)
            return _resume_existing(existing, name).unwrap()
        _rebuild_teardown(handle.client, existing, f"dvt/{name}:latest").unwrap()

    config = _load_config(config_file).unwrap()

    refuse_unsupported(config).unwrap()

    if "image" not in config:
        raise ValueError(
            f'{config_file} has no top-level "image" - only image-based '
            "devcontainer.json is supported"
        )

    features_config = config.get("features", {})
    feature_refs = list(features_config.keys())

    with httpx.Client() as http_client:
        pulled = traverse_result(
            feature_refs,
            lambda ref: pull_feature(http_client, ref, settings.data_dir / "features"),
        ).unwrap()

    features = [
        (_feature_id(ref), extracted_dir, features_config[ref])
        for ref, extracted_dir in zip(feature_refs, pulled, strict=True)
    ]

    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        podman_machine.ensure_gpu_support(
            handle.cli_binary, handle.machine_name
        ).unwrap()

    with tempfile.TemporaryDirectory() as scratch:
        image_tag = build_image(
            handle.client,
            config["image"],
            features,
            f"dvt/{name}:latest",
            Path(scratch),
            nocache=rebuild,
            pull=rebuild,
        ).unwrap()

    container = run_container(
        handle.client, image_tag, config, name, project_path, config_file
    ).unwrap()

    run_lifecycle_commands(container, config).unwrap()

    _refresh_ssh_config(name).unwrap()

    return container
```

Add `read_stored_config` and `config_has_drifted` to the `devtemplate.container` import block
near the top of `workspace.py` (currently `from devtemplate.container import (find_workspace_container, refuse_unsupported, run_container, run_lifecycle_commands)`):

```python
from devtemplate.container import (
    config_has_drifted,
    find_workspace_container,
    read_stored_config,
    refuse_unsupported,
    run_container,
    run_lifecycle_commands,
)
```

Also add a `DockerClient` import (used only for `_rebuild_teardown`'s type hint, matching how
`RuntimeHandle.client` itself is typed in `runtime.py`) alongside the existing `docker`-related
import:

```python
from docker.client import DockerClient
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_workspace.py -v`
Expected: PASS (all tests in the file, including the three updated resume tests and the five new
drift/rebuild tests).

- [ ] **Step 8: Commit**

```bash
git add src/devtemplate/workspace.py tests/test_workspace.py
git commit -m "feat(dvt): refuse to resume a drifted workspace, add --rebuild teardown path"
```

---

## Task 4: `dvt up --rebuild` CLI flag

**Files:**
- Modify: `src/devtemplate/cli.py:58-83`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `up_workspace(handle, settings, name, path, rebuild: bool = False)` from Task 3.

- [ ] **Step 1: Update the three existing `up_workspace` fake lambdas to accept `rebuild`**

In `tests/test_cli.py`, each of the following three lambdas currently reads
`lambda handle, settings, name, path: ...` — since `cli.py`'s `up` command will now always pass
`rebuild=rebuild` as a keyword argument, each needs a `rebuild=False` parameter added:

Line 41 (`test_up_builds_and_runs_workspace`):
```python
        lambda handle, settings, name, path, rebuild=False: cli_module.Ok(object()),
```

Line 72 (`test_up_passes_podman_machine_settings_to_get_client`):
```python
        lambda handle, settings, name, path, rebuild=False: cli_module.Ok(object()),
```

Line 96 (`test_up_reports_clean_error_on_failure`):
```python
        lambda handle, settings, name, path, rebuild=False: cli_module.Err(
            FileNotFoundError("no devcontainer.json")
        ),
```

Line 272 (`test_up_infers_name_from_the_single_matching_workspace`):
```python
        lambda handle, settings, name, path, rebuild=False: (
            captured.update(name=name) or cli_module.Ok(object())
        ),
```

- [ ] **Step 2: Write a new failing test asserting `--rebuild` threads through**

Add to `tests/test_cli.py`:

```python
def test_up_rebuild_flag_threads_through_to_up_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: (
            captured.update(rebuild=rebuild) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project", "--rebuild"])

    assert result.exit_code == 0
    assert captured["rebuild"] is True
```

- [ ] **Step 3: Run tests to verify the new test fails**

Run: `pytest tests/test_cli.py -k rebuild_flag -v`
Expected: FAIL with `Usage: ... Error: No such option: --rebuild` (exit code 2, not 0).

- [ ] **Step 4: Implement**

In `src/devtemplate/cli.py`, replace the `up` command (lines 58-83) with:

```python
@app.command()
def up(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name for the new workspace (default: inferred from the current folder).",
    ),
    rebuild: bool = typer.Option(  # noqa: B008
        False,
        "--rebuild",
        help="Force a fresh rebuild, discarding the existing container and cached image.",
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
    unwrap_or_exit(
        up_workspace(handle, settings, resolved_name, Path.cwd(), rebuild=rebuild), console
    )
    console.print(
        f"[green]Workspace '{escape(resolved_name)}' is up.[/green] "
        f"Connect with: dvt ssh {escape(resolved_name)} "
        f"(plain 'ssh {escape(resolved_name)}' also works, via the ~/.ssh/config entry dvt just wrote)"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/cli.py tests/test_cli.py
git commit -m "feat(dvt): add dvt up --rebuild flag"
```

---

## Task 5: Update `docs/content/commands.md`

**Files:**
- Modify: `docs/content/commands.md:75-82`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Replace the `up` behavior paragraph**

Replace lines 75-82 of `docs/content/commands.md`:

```markdown
`dvt up <name>` builds an image from cwd's `.devcontainer/devcontainer.json` — pulling
each referenced Feature as a real OCI artifact and baking it into a generated
multi-stage Dockerfile, exactly the way `@devcontainers/cli`/`devpod` themselves
build Features — then runs the container. `<name>` is the tag given to the
resulting workspace, not a path; run `up` from inside the project directory. If a
workspace with that name already exists, `up` starts it (if stopped) or leaves it
running (if already running) rather than rebuilding — delete and re-`up` to pick up
devcontainer.json changes.
```

with:

```markdown
`dvt up <name>` builds an image from cwd's `.devcontainer/devcontainer.json` — pulling
each referenced Feature as a real OCI artifact and baking it into a generated
multi-stage Dockerfile, exactly the way `@devcontainers/cli`/`devpod` themselves
build Features — then runs the container. `<name>` is the tag given to the
resulting workspace, not a path; run `up` from inside the project directory. If a
workspace with that name already exists, `up` starts it (if stopped) or leaves it
running (if already running), unless `devcontainer.json` has changed since that
container was built (compared against the config baked into the container's own
`devcontainer.metadata` label) — in which case `up` refuses and points at
`dvt up --rebuild`, rather than silently reusing a stale image.

`--rebuild` forces a from-scratch rebuild regardless of whether anything actually
changed: it removes the existing container and its cached image tag, then builds
fresh with Docker's build cache and base-image reuse both disabled, so a moved
upstream base image tag is picked up too. If the rebuild's own build fails, the old
container is already gone — unlike a plain `up`, which never destroys anything until
a replacement is confirmed working.
```

- [ ] **Step 2: Commit**

```bash
git add docs/content/commands.md
git commit -m "docs(dvt): document dvt up --rebuild"
```
