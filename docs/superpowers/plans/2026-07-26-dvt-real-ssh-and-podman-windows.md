# dvt Real SSH + Podman-Windows Machine Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two known gaps left by the native container runtime work: `dvt ssh --stdio` doesn't actually speak SSH (so real `ssh`, VS Code Remote-SSH, and JetBrains Gateway can't use it), and Podman-on-Windows was stubbed out entirely. Also: accept digest-pinned Feature refs, and bring GPU support on Podman-Windows to parity with Docker.

**Architecture:** A new `ssh_server.py` module runs a real (if minimal) `asyncssh` SSH server bound to `dvt ssh --stdio`'s own process via a `socket.socketpair()` bridge, with no real authentication (the pipe is only ever reachable by a local subprocess spawn) and each session bridged to a `docker`/`podman exec -i` subprocess. A new `podman_machine.py` module ports `devpod-podman-powershell`'s `init.ps1` machine-lifecycle logic into Python. `ssh.py` regains `write_ssh_config_entry`/`remove_ssh_config_entry` (removed during the native runtime work's final review, now restored since the underlying mechanism is fixed).

**Tech Stack:** `asyncssh` (new dependency), `socket.socketpair()`, `subprocess` (for `podman machine ...` subcommands and the bridged `docker exec`), `docker-py` (base_url now includes an `npipe://` form for Windows Podman machines).

Full design background: `docs/superpowers/specs/2026-07-26-dvt-real-ssh-and-podman-windows-design.md`.

## Global Constraints

- New dependency: `asyncssh`, added to `[project.dependencies]` in `dvt/pyproject.toml`, same place every other runtime dependency lives.
- New `Settings` fields: `podman_machine_auto_init: bool = False`, `podman_machine_auto_start: bool = True` (env vars `DVT_PODMAN_MACHINE_AUTO_INIT`/`DVT_PODMAN_MACHINE_AUTO_START`, matching the existing `DVT_`-prefixed convention).
- Every fallible function returns `Result[T, Exception]` — no bare exceptions escaping to callers. This has been the single most common review finding across the whole native-runtime plan (2-3 fix rounds in nearly every task) — **wrap every fallible operation's full scope in try/except from the first draft**, not just the "main" call. `subprocess.run` calls, dict/list access on parsed JSON, and socket operations are the recurring offenders.
- **This phase is scoped to Windows only** for the Podman-machine lifecycle (matching the design spec). macOS also uses a VM-backed `podman machine` rather than a native rootless socket, and `runtime.py`'s existing `_default_podman_socket()` fallback doesn't actually handle that correctly either — that is a **pre-existing, separate gap**, explicitly out of scope here. Do not fix it as a side effect; do not remove the (currently-wrong-for-macOS-podman-machine-setups, but out of scope) existing fallback.
- `dvt ssh <name>` typed directly (`exec_interactive` in `ssh.py`) is **unaffected** by this entire plan — it already works correctly (direct `docker`/`podman exec -it`, no SSH protocol involved) and needs no changes. Only `dvt ssh --stdio <name>` (the `ProxyCommand` target) changes.
- The asyncssh server bridge (Task 4) is the highest-risk, least-precedented piece in this plan — every API call it's built from (`socket.socketpair()`, `asyncssh.run_server(sock, ...)`, `SSHServer.begin_auth`, `process_factory`, `SSHServerProcess.stdin`/`.stdout`/`.exit()`, `asyncssh.generate_private_key`) was verified directly against `asyncssh`'s own source before this plan was written (not guessed from docs alone) — but the exact concurrency/bridging glue between the socketpair and this process's real stdin/stdout has NOT been run end-to-end before this plan was written. Task 4 requires a real, live test (a real client talking to a real server instance within the same test process) before it's considered done — a mocked test cannot catch a broken byte-pumping bridge.
- `requires-python = ">=3.12,<3.15"` is unchanged.

---

### Task 1: Digest-pinned Feature refs (`features.py`)

**Files:**
- Modify: `dvt/src/devtemplate/features.py`
- Test: `dvt/tests/test_features.py` (add coverage)

**Interfaces:**
- Modifies: `_parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]` (unchanged signature — the third tuple element is now documented as "tag or digest", not always a tag). No change to `pull_feature`'s public signature or any other function in this file.

- [ ] **Step 1: Write the failing tests**

Add to `dvt/tests/test_features.py`:

```python
def test_parse_feature_ref_accepts_digest_form():
    result = _parse_feature_ref(
        "ghcr.io/jesserobertson/devcontainers/fastapi@sha256:"
        "f26cbb9c85b8211fa150e50200d48033d82d6678b6c871e8c2db015a1d81ffff"
    )
    assert result.is_ok()
    registry, repository, reference = result.unwrap()
    assert registry == "ghcr.io"
    assert repository == "jesserobertson/devcontainers/fastapi"
    assert reference == (
        "sha256:f26cbb9c85b8211fa150e50200d48033d82d6678b6c871e8c2db015a1d81ffff"
    )


def test_parse_feature_ref_rejects_digest_form_missing_repository():
    result = _parse_feature_ref("ghcr.io/@sha256:abc")
    assert result.is_err()


def test_pull_feature_works_with_digest_ref(tmp_path):
    blob = _make_feature_tar()
    digest = "sha256:deadbeef"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/manifests/{digest}") and "authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith(f"/manifests/{digest}"):
            return httpx.Response(
                200,
                json={
                    "layers": [
                        {
                            "mediaType": "application/vnd.devcontainers.layer.v1+tar",
                            "digest": "sha256:layerdigest",
                            "size": len(blob),
                        }
                    ]
                },
            )
        if path.endswith("/blobs/sha256:layerdigest"):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = pull_feature(
        client, f"ghcr.io/jesserobertson/devcontainers/fastapi@{digest}", tmp_path
    )

    assert result.is_ok()
    assert (result.unwrap() / "devcontainer-feature.json").exists()
```

(`_make_feature_tar` already exists in this test file from Task 2 of the native runtime plan — reuse it, don't redefine it.)

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_features.py -v`
Expected: FAIL — `_parse_feature_ref` doesn't yet recognize the `@sha256:` form (falls into the `:tag` branch's `rpartition`, misparsing).

- [ ] **Step 3: Implement**

In `dvt/src/devtemplate/features.py`, replace `_parse_feature_ref`:

```python
def _parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]:
    """Split 'registry/repository/path:tag' or 'registry/repository/path@sha256:hex'
    into (registry, repository_path, reference), where reference is either a tag
    or a digest - OCI registries accept either directly in the manifest URL's
    final path segment, so no other code in this module needs to distinguish
    them. Digest form is checked first: 'sha256:hex' itself contains a colon,
    so checking for '@sha256:' before falling through to the tag-splitting
    rpartition avoids misparsing a digest ref as a malformed tag ref.
    """
    if "/" not in ref:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: expected registry/repository:tag")
        )
    registry, _, rest = ref.partition("/")
    if "@sha256:" in rest:
        repository, _, digest = rest.partition("@")
        if not repository or not digest:
            return Err(
                ValueError(f"Invalid feature ref {ref!r}: missing repository or digest")
            )
        return Ok((registry, repository, digest))
    if ":" not in rest:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: missing :tag or @sha256:digest")
        )
    repository, _, tag = rest.rpartition(":")
    if not repository or not tag:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: missing repository or tag")
        )
    return Ok((registry, repository, tag))
```

No other function in this file needs to change — `_get_token`, `_fetch_manifest`, `_fetch_and_extract_layer`, and `pull_feature` all already just thread the third tuple element through as an opaque URL path segment named `tag`; a digest string works identically there. (Optional, non-blocking cleanup: rename that parameter from `tag` to `reference` throughout the file for clarity, since it's no longer always a tag — do this only if it's a clean, mechanical rename with no behavior change.)

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_features.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/features.py dvt/tests/test_features.py
git commit -m "feat(dvt): accept digest-pinned Feature refs (ref@sha256:...)"
```

---

### Task 2: Podman-Windows machine lifecycle — detection, auto-start (`podman_machine.py`)

**Files:**
- Create: `dvt/src/devtemplate/podman_machine.py`
- Modify: `dvt/src/devtemplate/config.py` (add two `Settings` fields)
- Modify: `dvt/src/devtemplate/runtime.py` (wire in on `win32`)
- Test: `dvt/tests/test_podman_machine.py`
- Test: `dvt/tests/test_runtime.py` (add Windows-path coverage)
- Test: `dvt/tests/test_config.py` (add coverage for the two new fields)

**Interfaces:**
- Produces (from `devtemplate.podman_machine`):
  - `list_machines(cli_binary: str) -> Result[list[dict[str, Any]], Exception]`
  - `inspect_machine(cli_binary: str, name: str) -> Result[dict[str, Any], Exception]`
  - `start_machine(cli_binary: str, name: str) -> Result[None, Exception]`
  - `init_machine(cli_binary: str, name: str) -> Result[None, Exception]`
  - `wait_until_ready(cli_binary: str, timeout_seconds: int = 60) -> Result[None, Exception]`
  - `ensure_machine_ready(cli_binary: str, *, auto_start: bool, auto_init: bool) -> Result[tuple[str, str], Exception]` (returns `(machine_name, connection_url)`)
- Modifies: `RuntimeHandle` gains `machine_name: str | None = None`; `get_client` gains `podman_machine_auto_init`/`podman_machine_auto_start` keyword params; `_try_podman` is refactored to delegate to a new `_resolve_podman` that both `get_client`'s explicit-`"podman"` branch and the "auto" fallback use.

This task's wire format was verified against a real Podman machine on this
project's own Windows host before this plan was written:
`podman machine list --format json` and `podman machine inspect <name>` were
both run for real (see the design spec for the exact JSON). Use the field
names confirmed there (`Name`, `State`, `ConnectionInfo.PodmanPipe.Path`,
`ConnectionInfo.PodmanSocket.Path`) — not invented ones.

- [ ] **Step 1: Add the `Settings` fields**

In `dvt/src/devtemplate/config.py`, add two fields alongside `runtime`:

```python
    podman_machine_auto_init: bool = False
    podman_machine_auto_start: bool = True
```

Add to `dvt/tests/test_config.py`:

```python
def test_podman_machine_settings_defaults(settings):
    assert settings.podman_machine_auto_init is False
    assert settings.podman_machine_auto_start is True


def test_podman_machine_auto_init_reads_from_env(monkeypatch, settings):
    monkeypatch.setenv("DVT_PODMAN_MACHINE_AUTO_INIT", "true")
    from devtemplate.config import Settings

    assert Settings().podman_machine_auto_init is True
```

Run: `pixi run -e dev pytest tests/test_config.py -v` — expect these two to pass immediately (pydantic parses `bool` env vars automatically); this step just documents the new surface.

- [ ] **Step 2: Write the failing tests for `podman_machine.py`**

Create `dvt/tests/test_podman_machine.py`:

```python
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from devtemplate.podman_machine import (
    ensure_machine_ready,
    inspect_machine,
    list_machines,
)

RUNNING_MACHINE = {
    "Name": "devpod-machine",
    "Default": True,
    "Running": True,
    "VMType": "wsl",
}

STOPPED_MACHINE = {**RUNNING_MACHINE, "Running": False}

INSPECT_RUNNING = [
    {
        "Name": "devpod-machine",
        "State": "running",
        "ConnectionInfo": {
            "PodmanPipe": {"Path": r"\\.\pipe\podman-devpod-machine"},
            "PodmanSocket": {"Path": "/tmp/podman/devpod-machine-api.sock"},
        },
    }
]

INSPECT_STOPPED = [{**INSPECT_RUNNING[0], "State": "stopped"}]


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_list_machines_parses_json():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout=json.dumps([RUNNING_MACHINE])),
    ):
        result = list_machines("podman")
    assert result.is_ok()
    assert result.unwrap() == [RUNNING_MACHINE]


def test_list_machines_returns_err_on_failure():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(1, stderr="boom"),
    ):
        result = list_machines("podman")
    assert result.is_err()


def test_inspect_machine_parses_first_entry():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout=json.dumps(INSPECT_RUNNING)),
    ):
        result = inspect_machine("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap()["State"] == "running"


def test_ensure_machine_ready_no_machines_refuses_without_auto_init():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="[]"),
    ):
        result = ensure_machine_ready("podman", auto_start=True, auto_init=False)
    assert result.is_err()


def test_ensure_machine_ready_already_running_resolves_connection_url():
    def fake_run(args, **kwargs):
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([RUNNING_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_RUNNING))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_machine_ready("podman", auto_start=True, auto_init=False)

    assert result.is_ok()
    name, url = result.unwrap()
    assert name == "devpod-machine"
    assert url == "npipe:////./pipe/podman-devpod-machine"


def test_ensure_machine_ready_stopped_without_auto_start_refuses():
    def fake_run(args, **kwargs):
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([STOPPED_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_STOPPED))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_machine_ready("podman", auto_start=False, auto_init=False)

    assert result.is_err()


def test_ensure_machine_ready_stopped_with_auto_start_starts_it():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([STOPPED_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_STOPPED))
        if args[1:3] == ["machine", "start"]:
            return _fake_run(0)
        if args[1] == "ps":
            return _fake_run(0, stdout="[]")
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        with patch("devtemplate.podman_machine.time.sleep"):
            result = ensure_machine_ready("podman", auto_start=True, auto_init=False)

    assert result.is_ok()
    assert ["podman", "machine", "start", "devpod-machine"] in calls


def test_ensure_machine_ready_no_machines_with_auto_init_creates_one():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout="[]")
        if args[1:3] == ["machine", "init"]:
            return _fake_run(0)
        if args[1:3] == ["machine", "start"]:
            return _fake_run(0)
        if args[1] == "ps":
            return _fake_run(0, stdout="[]")
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_RUNNING))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        with patch("devtemplate.podman_machine.time.sleep"):
            result = ensure_machine_ready("podman", auto_start=True, auto_init=True)

    assert result.is_ok()
    assert any(c[1:3] == ["machine", "init"] for c in calls)
```

- [ ] **Step 3: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_podman_machine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.podman_machine'`.

- [ ] **Step 4: Implement `podman_machine.py`**

Create `dvt/src/devtemplate/podman_machine.py`:

```python
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from logerr import Err, Ok, Result

_DEFAULT_MACHINE_NAME = "dvt-machine"
_DEFAULT_CPUS = 2
_DEFAULT_MEMORY_MB = 4096
_DEFAULT_DISK_GB = 100
_NAMED_PIPE_PATTERN = re.compile(r"\\\\\.\\pipe\\(.+)$")


def _run_podman_json(cli_binary: str, args: list[str]) -> Result[Any, Exception]:
    try:
        result = subprocess.run(
            [cli_binary, *args], capture_output=True, text=True
        )
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"podman {' '.join(args)} failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )
            )
        return Ok(json.loads(result.stdout))
    except Exception as exc:
        return Err(exc)


def list_machines(cli_binary: str) -> Result[list[dict[str, Any]], Exception]:
    return _run_podman_json(cli_binary, ["machine", "list", "--format", "json"])


def inspect_machine(cli_binary: str, name: str) -> Result[dict[str, Any], Exception]:
    result = _run_podman_json(cli_binary, ["machine", "inspect", name])
    if result.is_err():
        return Err(result.unwrap_err())
    inspected = result.unwrap()
    if not isinstance(inspected, list) or not inspected or not isinstance(inspected[0], dict):
        return Err(
            ValueError(f"podman machine inspect {name!r} returned unexpected shape: {inspected!r}")
        )
    return Ok(inspected[0])


def start_machine(cli_binary: str, name: str) -> Result[None, Exception]:
    try:
        result = subprocess.run(
            [cli_binary, "machine", "start", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            return Err(RuntimeError(f"Failed to start machine {name!r}: {result.stderr.strip()}"))
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def init_machine(cli_binary: str, name: str) -> Result[None, Exception]:
    try:
        result = subprocess.run(
            [
                cli_binary, "machine", "init", name,
                "--cpus", str(_DEFAULT_CPUS),
                "--memory", str(_DEFAULT_MEMORY_MB),
                "--disk-size", str(_DEFAULT_DISK_GB),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return Err(RuntimeError(f"Failed to init machine {name!r}: {result.stderr.strip()}"))
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def wait_until_ready(cli_binary: str, timeout_seconds: int = 60) -> Result[None, Exception]:
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = subprocess.run([cli_binary, "ps"], capture_output=True, text=True)
            if result.returncode == 0:
                return Ok(None)
            time.sleep(2)
        return Err(RuntimeError(f"Machine did not become ready within {timeout_seconds}s"))
    except Exception as exc:
        return Err(exc)


def _connection_url(inspected: dict[str, Any]) -> Result[str, Exception]:
    """Translate `podman machine inspect`'s ConnectionInfo into a docker-py
    base_url. Verified directly against a real machine: PodmanPipe.Path looks
    like '\\\\.\\pipe\\podman-devpod-machine' on Windows; docker-py expects
    'npipe:////./pipe/podman-devpod-machine'. Falls back to PodmanSocket for
    non-Windows callers of this function (WSL-internal use), though this
    module is only invoked from runtime.py on win32 - see Global Constraints."""
    connection_info = inspected.get("ConnectionInfo")
    if not isinstance(connection_info, dict):
        return Err(ValueError(f"machine inspect result has no ConnectionInfo: {inspected!r}"))
    pipe = connection_info.get("PodmanPipe")
    if isinstance(pipe, dict) and isinstance(pipe.get("Path"), str):
        match = _NAMED_PIPE_PATTERN.match(pipe["Path"])
        if match:
            return Ok(f"npipe:////./pipe/{match.group(1)}")
    socket_info = connection_info.get("PodmanSocket")
    if isinstance(socket_info, dict) and isinstance(socket_info.get("Path"), str):
        return Ok(f"unix://{socket_info['Path']}")
    return Err(
        ValueError(f"machine inspect result has no usable connection endpoint: {connection_info!r}")
    )


def _inspect_and_connect(cli_binary: str, name: str) -> Result[tuple[str, str], Exception]:
    inspect_result = inspect_machine(cli_binary, name)
    if inspect_result.is_err():
        return Err(inspect_result.unwrap_err())
    url_result = _connection_url(inspect_result.unwrap())
    if url_result.is_err():
        return Err(url_result.unwrap_err())
    return Ok((name, url_result.unwrap()))


def _start_and_connect(cli_binary: str, name: str) -> Result[tuple[str, str], Exception]:
    start_result = start_machine(cli_binary, name)
    if start_result.is_err():
        return Err(start_result.unwrap_err())
    ready_result = wait_until_ready(cli_binary)
    if ready_result.is_err():
        return Err(ready_result.unwrap_err())
    return _inspect_and_connect(cli_binary, name)


def ensure_machine_ready(
    cli_binary: str, *, auto_start: bool, auto_init: bool
) -> Result[tuple[str, str], Exception]:
    """Detect a Podman machine, auto-start it if stopped (when auto_start),
    refuse to auto-create one unless auto_init is set, and resolve its
    connection URL. Returns (machine_name, connection_url)."""
    machines_result = list_machines(cli_binary)
    if machines_result.is_err():
        return Err(machines_result.unwrap_err())
    machines = machines_result.unwrap()

    if not machines:
        if not auto_init:
            return Err(
                RuntimeError(
                    "No Podman machine found. Run 'podman machine init' first, "
                    "or set DVT_PODMAN_MACHINE_AUTO_INIT=true."
                )
            )
        init_result = init_machine(cli_binary, _DEFAULT_MACHINE_NAME)
        if init_result.is_err():
            return Err(init_result.unwrap_err())
        return _start_and_connect(cli_binary, _DEFAULT_MACHINE_NAME)

    name = machines[0]["Name"].rstrip("*")
    inspect_result = inspect_machine(cli_binary, name)
    if inspect_result.is_err():
        return Err(inspect_result.unwrap_err())
    inspected = inspect_result.unwrap()

    if inspected.get("State") == "running":
        url_result = _connection_url(inspected)
        if url_result.is_err():
            return Err(url_result.unwrap_err())
        return Ok((name, url_result.unwrap()))

    if not auto_start:
        return Err(
            RuntimeError(
                f"Machine {name!r} is not running. Run 'podman machine start {name}' first, "
                "or set DVT_PODMAN_MACHINE_AUTO_START=true."
            )
        )
    return _start_and_connect(cli_binary, name)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_podman_machine.py -v`
Expected: all PASS.

- [ ] **Step 6: Wire into `runtime.py`**

Modify `dvt/src/devtemplate/runtime.py`:

```python
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

import docker
from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate import podman_machine


@dataclass(frozen=True)
class RuntimeHandle:
    """A resolved container runtime: a docker-py client plus which engine and CLI
    binary it talks to. cli_binary is only used by ssh.py's interactive exec
    plumbing, which shells out to the bundled docker/podman CLI rather than
    proxying raw stdio through docker-py's own exec/attach socket API - see
    ssh.py's module docstring. machine_name is set only for a Windows Podman
    machine (see podman_machine.py); None for Docker or non-Windows Podman."""

    client: DockerClient
    engine: Literal["docker", "podman"]
    cli_binary: str
    machine_name: str | None = None


def _try_docker() -> RuntimeHandle | None:
    cli_binary = shutil.which("docker")
    if cli_binary is None:
        return None
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None
    return RuntimeHandle(client=client, engine="docker", cli_binary=cli_binary)


def _default_podman_socket() -> str | None:
    """Best-effort default rootless Podman socket path on Linux. Also wrong for
    a macOS podman-machine setup - a separate, pre-existing gap, out of scope
    for this plan (see Global Constraints)."""
    if sys.platform == "win32" or not hasattr(os, "getuid"):
        return None
    return f"unix:///run/user/{os.getuid()}/podman/podman.sock"


def _resolve_podman(
    *, auto_init: bool, auto_start: bool
) -> Result[RuntimeHandle, Exception]:
    cli_binary = shutil.which("podman")
    if cli_binary is None:
        return Err(FileNotFoundError("podman not found on PATH"))
    if sys.platform == "win32":
        machine_result = podman_machine.ensure_machine_ready(
            cli_binary, auto_start=auto_start, auto_init=auto_init
        )
        if machine_result.is_err():
            return Err(machine_result.unwrap_err())
        machine_name, socket_url = machine_result.unwrap()
    else:
        machine_name = None
        socket_url = os.environ.get("CONTAINER_HOST") or _default_podman_socket()
        if socket_url is None:
            return Err(
                RuntimeError(
                    "Podman socket not found (tried CONTAINER_HOST / default rootless path)"
                )
            )
    try:
        client = docker.DockerClient(base_url=socket_url)
        client.ping()
    except Exception as exc:
        return Err(exc)
    return Ok(
        RuntimeHandle(
            client=client, engine="podman", cli_binary=cli_binary, machine_name=machine_name
        )
    )


def _try_podman(*, auto_init: bool = False, auto_start: bool = True) -> RuntimeHandle | None:
    result = _resolve_podman(auto_init=auto_init, auto_start=auto_start)
    return result.unwrap() if result.is_ok() else None


def get_client(
    runtime: Literal["auto", "docker", "podman"],
    *,
    podman_machine_auto_init: bool = False,
    podman_machine_auto_start: bool = True,
) -> Result[RuntimeHandle, Exception]:
    """Resolve a container runtime per Settings.runtime. "auto" tries Docker's
    endpoint first, then Podman's compatible socket. An explicit "podman"
    request surfaces Podman-specific errors directly (e.g. "no machine found")
    rather than the generic message "auto" falls back to on double failure."""
    if runtime == "docker":
        handle = _try_docker()
        if handle is None:
            return Err(
                RuntimeError("Docker not reachable (tried DOCKER_HOST / platform default)")
            )
        return Ok(handle)
    if runtime == "podman":
        return _resolve_podman(
            auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
        )
    handle = _try_docker() or _try_podman(
        auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
    )
    if handle is None:
        return Err(RuntimeError("No container runtime found (tried Docker, Podman)"))
    return Ok(handle)
```

Add to `dvt/tests/test_runtime.py`:

```python
def test_get_client_podman_explicit_surfaces_specific_windows_error(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_module.podman_machine,
        "ensure_machine_ready",
        lambda cli_binary, auto_start, auto_init: runtime_module.Err(
            RuntimeError("No Podman machine found. Run 'podman machine init' first.")
        ),
    )

    result = runtime_module.get_client("podman")

    assert result.is_err()
    assert "No Podman machine found" in str(result.unwrap_err())


def test_get_client_podman_windows_success_sets_machine_name(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_module.podman_machine,
        "ensure_machine_ready",
        lambda cli_binary, auto_start, auto_init: runtime_module.Ok(
            ("devpod-machine", "npipe:////./pipe/podman-devpod-machine")
        ),
    )
    captured = {}

    def fake_docker_client(base_url):
        captured["base_url"] = base_url
        return _FakeClient()

    monkeypatch.setattr(runtime_module.docker, "DockerClient", fake_docker_client)

    result = runtime_module.get_client(
        "podman", podman_machine_auto_init=True, podman_machine_auto_start=True
    )

    assert result.is_ok()
    handle = result.unwrap()
    assert handle.machine_name == "devpod-machine"
    assert captured["base_url"] == "npipe:////./pipe/podman-devpod-machine"
```

(`_FakeClient` already exists in this test file from the native runtime plan — reuse it.)

- [ ] **Step 7: Wire the new settings through every `get_client` call site**

`get_client`'s new keyword params default to the same values `Settings`
itself defaults to, so nothing breaks immediately — but every existing call
site passes only `settings.runtime`, meaning `DVT_PODMAN_MACHINE_AUTO_INIT`/
`DVT_PODMAN_MACHINE_AUTO_START` would silently have no effect at all if this
step is skipped. In `dvt/src/devtemplate/cli.py`, update all four call sites
(`up`, `ssh`, `stop`, `delete` — currently `get_client(settings.runtime)`) to:

```python
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
```

Add a test to `dvt/tests/test_cli.py` (e.g. on the `up` command) that mocks
`get_client` and asserts it was called with both new keyword arguments
matching `settings`'s values — not just that `get_client` was called at all.

- [ ] **Step 8: Run to verify it passes, full quality check**

Run: `pixi run -e dev pytest tests/test_runtime.py tests/test_podman_machine.py tests/test_config.py tests/test_cli.py -v`
Expected: all PASS.

Run: `pixi run -e dev quality check`
Expected: mypy strict / ruff lint / ruff format all Pass.

- [ ] **Step 9: Commit**

```bash
git add dvt/src/devtemplate/podman_machine.py dvt/src/devtemplate/config.py dvt/src/devtemplate/runtime.py dvt/src/devtemplate/cli.py dvt/tests/test_podman_machine.py dvt/tests/test_runtime.py dvt/tests/test_config.py dvt/tests/test_cli.py
git commit -m "feat(dvt): Podman-Windows machine detection and auto-start"
```

---

### Task 3: Podman-Windows GPU/NVIDIA CDI setup

**Files:**
- Modify: `dvt/src/devtemplate/podman_machine.py`
- Modify: `dvt/src/devtemplate/workspace.py`
- Test: `dvt/tests/test_podman_machine.py` (add coverage)
- Test: `dvt/tests/test_workspace.py` (add coverage)

**Interfaces:**
- Produces (from `devtemplate.podman_machine`):
  - `check_gpu_cdi_ready(cli_binary: str, machine_name: str) -> Result[bool, Exception]`
  - `install_nvidia_toolkit(cli_binary: str, machine_name: str) -> Result[None, Exception]`
  - `ensure_gpu_support(cli_binary: str, machine_name: str) -> Result[None, Exception]`
- Consumes: `RuntimeHandle.machine_name` (Task 2) in `workspace.py`'s `up_workspace`.

Commands are ported verbatim from `devpod-podman-powershell`'s `init.ps1` (the
project's own reference implementation for this exact problem) — the CDI
check (`test -f /etc/cdi/nvidia.yaml`) and the toolkit install command
(`curl` the repo file, `dnf install`, `nvidia-ctk cdi generate`), both run via
`podman machine ssh <name> "<command>"`.

- [ ] **Step 1: Write the failing tests**

Add to `dvt/tests/test_podman_machine.py`:

```python
from devtemplate.podman_machine import (
    check_gpu_cdi_ready,
    ensure_gpu_support,
    install_nvidia_toolkit,
)


def test_check_gpu_cdi_ready_true_when_exists():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="exists\n"),
    ):
        result = check_gpu_cdi_ready("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap() is True


def test_check_gpu_cdi_ready_false_when_missing():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="missing\n"),
    ):
        result = check_gpu_cdi_ready("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap() is False


def test_ensure_gpu_support_skips_install_when_already_ready():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _fake_run(0, stdout="exists\n")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_gpu_support("podman", "devpod-machine")

    assert result.is_ok()
    assert len(calls) == 1  # only the check, no install


def test_ensure_gpu_support_installs_when_missing():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "test -f /etc/cdi/nvidia.yaml" in args[-1]:
            return _fake_run(0, stdout="missing\n")
        return _fake_run(0)

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_gpu_support("podman", "devpod-machine")

    assert result.is_ok()
    assert len(calls) == 2
    assert "nvidia-ctk cdi generate" in calls[1][-1]


def test_install_nvidia_toolkit_returns_err_on_failure():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(1, stderr="ssh failed"),
    ):
        result = install_nvidia_toolkit("podman", "devpod-machine")
    assert result.is_err()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_podman_machine.py -v`
Expected: FAIL — `check_gpu_cdi_ready`/`install_nvidia_toolkit`/`ensure_gpu_support` don't exist yet.

- [ ] **Step 3: Implement**

Add to `dvt/src/devtemplate/podman_machine.py`:

```python
_NVIDIA_TOOLKIT_INSTALL_COMMAND = (
    "sudo curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/"
    "nvidia-container-toolkit.repo -o /etc/yum.repos.d/nvidia-container-toolkit.repo "
    "&& sudo dnf install -y nvidia-container-toolkit "
    "&& sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
)


def check_gpu_cdi_ready(cli_binary: str, machine_name: str) -> Result[bool, Exception]:
    try:
        result = subprocess.run(
            [
                cli_binary, "machine", "ssh", machine_name,
                "test -f /etc/cdi/nvidia.yaml && echo exists || echo missing",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"Failed to check CDI status on {machine_name!r}: {result.stderr.strip()}"
                )
            )
        return Ok("exists" in result.stdout)
    except Exception as exc:
        return Err(exc)


def install_nvidia_toolkit(cli_binary: str, machine_name: str) -> Result[None, Exception]:
    try:
        result = subprocess.run(
            [cli_binary, "machine", "ssh", machine_name, _NVIDIA_TOOLKIT_INSTALL_COMMAND],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"Failed to install NVIDIA Container Toolkit on {machine_name!r}: "
                    f"{result.stderr.strip()}"
                )
            )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def ensure_gpu_support(cli_binary: str, machine_name: str) -> Result[None, Exception]:
    ready_result = check_gpu_cdi_ready(cli_binary, machine_name)
    if ready_result.is_err():
        return Err(ready_result.unwrap_err())
    if ready_result.unwrap():
        return Ok(None)
    return install_nvidia_toolkit(cli_binary, machine_name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_podman_machine.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire into `workspace.py`**

In `dvt/src/devtemplate/workspace.py`, add the GPU check before `build_image` runs, only when the engine is Podman-on-Windows (`handle.machine_name is not None`) and the config actually requests a GPU (`runArgs` contains `"--gpus"`):

```python
from devtemplate import podman_machine
```

Insert, after the `features`/`feature_refs` block and before the `build_image` call:

```python
    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        gpu_result = podman_machine.ensure_gpu_support(handle.cli_binary, handle.machine_name)
        if gpu_result.is_err():
            return Err(gpu_result.unwrap_err())
```

Add to `dvt/tests/test_workspace.py`:

```python
def test_up_workspace_ensures_gpu_support_on_podman_windows(project, settings, monkeypatch):
    handle = RuntimeHandle(
        client=MagicMock(), engine="podman", cli_binary="/usr/bin/podman", machine_name="devpod-machine"
    )
    (project / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "jax",
                "image": "ghcr.io/jesserobertson/base-cuda:latest",
                "runArgs": ["--gpus", "all"],
            }
        )
    )
    monkeypatch.setattr(workspace_module, "find_workspace_container", lambda client, name: None)
    calls = []
    monkeypatch.setattr(
        workspace_module.podman_machine,
        "ensure_gpu_support",
        lambda cli_binary, machine_name: calls.append(machine_name) or workspace_module.Ok(None),
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: workspace_module.Ok("dvt/jax:latest")
    )
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: workspace_module.Ok(MagicMock())
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: workspace_module.Ok(None)
    )

    result = up_workspace(handle, settings, "jax", project)

    assert result.is_ok()
    assert calls == ["devpod-machine"]


def test_up_workspace_skips_gpu_check_on_docker(project, settings, monkeypatch):
    handle = RuntimeHandle(client=MagicMock(), engine="docker", cli_binary="/usr/bin/docker")
    monkeypatch.setattr(workspace_module, "find_workspace_container", lambda client, name: None)
    monkeypatch.setattr(
        workspace_module, "pull_feature", lambda client, ref, cache_dir: workspace_module.Ok(Path("/x"))
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: workspace_module.Ok("dvt/fastapi:latest")
    )
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: workspace_module.Ok(MagicMock())
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: workspace_module.Ok(None)
    )
    ensure_gpu_calls = []
    monkeypatch.setattr(
        workspace_module.podman_machine,
        "ensure_gpu_support",
        lambda *a, **k: ensure_gpu_calls.append(1) or workspace_module.Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert ensure_gpu_calls == []
```

- [ ] **Step 6: Run to verify it passes, full quality check**

Run: `pixi run -e dev pytest tests/test_workspace.py tests/test_podman_machine.py -v`
Expected: all PASS.

Run: `pixi run -e dev quality check`
Expected: all Pass.

- [ ] **Step 7: Commit**

```bash
git add dvt/src/devtemplate/podman_machine.py dvt/src/devtemplate/workspace.py dvt/tests/test_podman_machine.py dvt/tests/test_workspace.py
git commit -m "feat(dvt): auto-install NVIDIA Container Toolkit for GPU templates on Podman-Windows"
```

---

### Task 4: Real SSH server (`ssh_server.py`)

**Files:**
- Create: `dvt/src/devtemplate/ssh_server.py`
- Modify: `dvt/pyproject.toml` (add `asyncssh` dependency)
- Test: `dvt/tests/test_ssh_server.py`

**Interfaces:**
- Produces: `run_stdio_server(cli_binary: str, container_name: str) -> int` from `devtemplate.ssh_server` — runs a real SSH server bound to this process's own stdin/stdout, bridging each opened session to `docker`/`podman exec -i <container_name> sh`. Returns the bridged process's exit code. Consumed by `ssh.py`'s `stdio_proxy` (Task 5).
- Consumes: nothing from earlier tasks in this plan (standalone).

**This is the highest-risk task in this plan.** Every API call below was
verified directly against `asyncssh`'s own source (not just its docs) before
this plan was written:
- `socket.socketpair()` — a connected pair of sockets, cross-platform including
  Windows (CPython has supported this since 3.5 via an internal loopback-based
  emulation on platforms without native `AF_UNIX` socketpair support).
- `asyncssh.run_server(sock, server_factory=..., server_host_keys=[...], process_factory=...)`
  — starts an SSH server on an *already-connected* socket (confirmed via its
  docstring: "can be used instead of `listen()` when connections are accepted
  outside of asyncio").
- `asyncssh.SSHServer.begin_auth(username) -> bool` — returning `False` means
  "no authentication required, immediately succeed" (confirmed directly from
  the base class's own docstring).
- `process_factory: Callable[[SSHServerProcess], Awaitable[None]]` — a
  coroutine receiving an `SSHServerProcess` with `.stdin`/`.stdout` (asyncssh
  stream objects) and `.exit(status: int)`.
- `asyncssh.generate_private_key("ssh-ed25519")` — generates an `SSHKey`
  usable directly as a `server_host_keys` entry.

**What was NOT verified before this plan was written:** the exact concurrency
glue bridging `socketpair()`'s other end to this process's *real* stdin/stdout
file descriptors, and bridging `SSHServerProcess`'s stdin/stdout to a spawned
`docker exec -i` subprocess's pipes. Both are standard, well-understood
patterns (thread-based blocking I/O pumps; `asyncio.create_subprocess_exec`
with `PIPE`s bridged via `async for`/`write()` loops) but have not been run
end-to-end before this plan was written. **This task is not done until a
live test proves bytes flow correctly in both directions** — a fully-mocked
test would not catch a broken bridge.

- [ ] **Step 1: Add the `asyncssh` dependency**

In `dvt/pyproject.toml`, add to `[project] dependencies`:

```toml
    "asyncssh>=2.14",
```

Run `pixi update asyncssh` (or `pixi install`) from `dvt/` so the lockfile picks it up.

- [ ] **Step 2: Write a live (not mocked) test first**

Create `dvt/tests/test_ssh_server.py`. This test starts a *real* server via
`run_stdio_server`'s internal building blocks in-process (not through actual
process stdin/stdout, which pytest can't easily control) and drives it with a
*real* `asyncssh` client, proving the SSH protocol negotiation and the
bridge to a stand-in subprocess both genuinely work:

```python
from __future__ import annotations

import asyncio
import socket

import asyncssh
import pytest

from devtemplate.ssh_server import _NoAuthServer, _handle_process


@pytest.mark.asyncio
async def test_server_and_client_exchange_bytes_over_a_real_ssh_session():
    """Proves the SSH protocol negotiation and the process-bridging plumbing
    both genuinely work: a real asyncssh client connects to a real asyncssh
    server over a real (loopback, in-process) socket pair, requests a shell,
    and the bridged subprocess's output round-trips back to the client. This
    is exactly the class of bug a fully-mocked test cannot catch - the
    original ProxyCommand design looked correct in isolation and only failed
    when a real ssh client actually tried to negotiate the protocol.
    """
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        # Stand-in for the real docker-exec bridge (Step 4) - just echoes
        # one line back, proving stdin -> process -> stdout round-trips
        # through the real SSH channel.
        line = await process.stdin.readline()
        process.stdout.write(f"echo:{line}")
        process.exit(0)

    server_task = asyncio.create_task(
        asyncssh.run_server(
            server_sock,
            server_factory=_NoAuthServer,
            server_host_keys=[host_key],
            process_factory=process_factory,
        )
    )

    async with asyncssh.connect(
        sock=client_sock, known_hosts=None, username="anyone"
    ) as conn:
        process = await conn.create_process()
        process.stdin.write("hello\n")
        process.stdin.write_eof()
        output = await process.stdout.read()

    assert output == "echo:hello\n"
    server_task.cancel()
```

Add `pytest-asyncio` to the `dev` pixi feature in `dvt/pyproject.toml` if it
isn't already a dependency (check first), and add
`[tool.pytest.ini_options] asyncio_mode = "auto"` (or the equivalent marker
registration your `pytest-asyncio` version needs) so `@pytest.mark.asyncio`
tests run without extra per-test configuration.

- [ ] **Step 3: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_ssh_server.py -v`
Expected: FAIL — `devtemplate.ssh_server` doesn't exist yet (or, once the
module exists with a stub, verify the actual SSH exchange fails until the
real implementation is in place).

- [ ] **Step 4: Implement `ssh_server.py`**

Create `dvt/src/devtemplate/ssh_server.py`:

```python
from __future__ import annotations

import asyncio
import socket
import threading

import asyncssh


class _NoAuthServer(asyncssh.SSHServer):
    """No real authentication - see this plan's design spec for why: the
    socketpair this server listens on is only ever reachable by a local
    subprocess spawn, which already requires local shell access (the actual
    security boundary). Requiring SSH-level auth on top would check a
    credential that adds no real security."""

    def begin_auth(self, username: str) -> bool:
        return False


async def _handle_process(
    process: asyncssh.SSHServerProcess, cli_binary: str, container_name: str
) -> None:
    """Bridge one opened SSH session to a `docker/podman exec -i` subprocess -
    the same exec mechanism `exec_interactive` already uses, just with pipes
    instead of inherited stdio (asyncssh owns the actual terminal now)."""
    proc = await asyncio.create_subprocess_exec(
        cli_binary, "exec", "-i", container_name, "sh",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    async def pump_client_to_process() -> None:
        async for data in process.stdin:
            proc.stdin.write(data.encode() if isinstance(data, str) else data)
            await proc.stdin.drain()
        proc.stdin.close()

    async def pump_process_to_client() -> None:
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            process.stdout.write(chunk.decode(errors="replace"))

    await asyncio.gather(pump_client_to_process(), pump_process_to_client())
    exit_code = await proc.wait()
    process.exit(exit_code)


def _pump_stdio_to_socket(sock: socket.socket) -> None:
    """Bridge this process's real stdin/stdout to the socketpair end asyncssh
    isn't using. Runs in dedicated threads since it's blocking I/O on real
    file descriptors, alongside the asyncio event loop driving the server on
    the other end of the pair."""
    import sys

    def stdin_to_sock() -> None:
        while True:
            data = sys.stdin.buffer.read(4096)
            if not data:
                break
            sock.sendall(data)
        sock.shutdown(socket.SHUT_WR)

    def sock_to_stdout() -> None:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    reader = threading.Thread(target=stdin_to_sock, daemon=True)
    writer = threading.Thread(target=sock_to_stdout, daemon=True)
    reader.start()
    writer.start()
    writer.join()  # server-side close (EOF from the socket) ends the bridge


def run_stdio_server(cli_binary: str, container_name: str) -> int:
    """Run a real (if minimal) SSH server bound to this process's own
    stdin/stdout, bridging the one session it'll ever handle to
    `docker/podman exec -i <container_name> sh`. Returns the bridged
    process's exit code."""
    exit_code = 0

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        nonlocal exit_code
        await _handle_process(process, cli_binary, container_name)

    async def main() -> None:
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        server_sock, stdio_sock = socket.socketpair()
        bridge_thread = threading.Thread(
            target=_pump_stdio_to_socket, args=(stdio_sock,), daemon=True
        )
        bridge_thread.start()
        await asyncssh.run_server(
            server_sock,
            server_factory=_NoAuthServer,
            server_host_keys=[host_key],
            process_factory=process_factory,
        )
        bridge_thread.join()

    asyncio.run(main())
    return exit_code
```

**Note for the implementer:** the `nonlocal exit_code` wiring above is a
sketch, not verified end-to-end — `SSHServerProcess.exit()` sets the *SSH
channel's* exit status (visible to the connecting client), it does not
straightforwardly hand a value back to this function's own return. You will
likely need `_handle_process` to return the exit code directly, or store it
in a shared mutable (e.g. a one-element list, or an `asyncio.Future`) that
`main()` reads after `run_server` completes. Verify this against the real
test in Step 2 - if `run_stdio_server`'s return value doesn't actually
reflect the subprocess's exit code, fix the wiring until it does, and extend
Step 2's test to assert on it directly (e.g. have the stand-in process exit
non-zero and confirm the caller observes that).

- [ ] **Step 5: Run to verify the live test passes**

Run: `pixi run -e dev pytest tests/test_ssh_server.py -v`
Expected: PASS — real bytes genuinely round-tripped through a real SSH
session. Do not proceed to Step 6 until this is genuinely green, including
after any wiring fixes from the note above.

- [ ] **Step 6: Add a test for `run_stdio_server` itself**

```python
def test_run_stdio_server_bridges_to_real_docker_exec(monkeypatch):
    """Unlike Step 2's test (which drives the internal building blocks
    directly), this exercises run_stdio_server's own stdin/stdout bridging by
    replacing sys.stdin/sys.stdout with in-memory buffers wired to a live
    asyncssh client running in a background thread - proving the full
    function, not just its internals, works end to end without a real
    docker/podman daemon (the exec subprocess itself is mocked)."""
    # Implementer: design this test to genuinely exercise run_stdio_server()
    # as a black box (patching subprocess creation for the docker-exec half,
    # since a real daemon isn't required to prove the SSH+bridging logic,
    # but NOT mocking asyncssh itself) rather than testing only the already-
    # covered internals from Step 2. If this proves impractical within
    # reasonable effort, at minimum add a comment explaining why and rely on
    # Step 2's coverage plus Task 6's real end-to-end integration test.
```

- [ ] **Step 7: Full quality check**

Run: `pixi run -e dev quality check`
Expected: mypy strict / ruff lint / ruff format all Pass. (`asyncssh` ships
type stubs; if mypy strict flags anything in `asyncssh`'s own stubs as
opposed to this module's code, a targeted `# type: ignore[...]` with a
comment is acceptable - don't weaken `disallow_untyped_defs` project-wide
for this one dependency.)

- [ ] **Step 8: Commit**

```bash
git add dvt/pyproject.toml dvt/src/devtemplate/ssh_server.py dvt/tests/test_ssh_server.py
git commit -m "feat(dvt): real SSH server for dvt ssh --stdio (asyncssh, no sshd baked into images)"
```

---

### Task 5: Restore `ssh.py`'s config functions, wire `stdio_proxy`

**Files:**
- Modify: `dvt/src/devtemplate/ssh.py`
- Modify: `dvt/src/devtemplate/workspace.py`
- Modify: `dvt/src/devtemplate/cli.py`
- Test: `dvt/tests/test_ssh.py`
- Test: `dvt/tests/test_workspace.py`
- Test: `dvt/tests/test_cli.py`

**Interfaces:**
- Produces (from `devtemplate.ssh`): `write_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]`, `remove_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]`, `stdio_proxy(cli_binary: str, client: DockerClient, name: str) -> Result[int, Exception]`.
- Consumes: `run_stdio_server` (Task 4), `find_workspace_container` (already imported in `ssh.py`, unchanged).

This restores code that existed before the native runtime plan's final
review removed it (the `ProxyCommand` shape itself was always correct; only
the far end needed to actually speak SSH, which Task 4 now provides).

- [ ] **Step 1: Write the failing tests**

Add to `dvt/tests/test_ssh.py`:

```python
def test_write_ssh_config_entry_adds_host_block(tmp_path):
    config_path = tmp_path / "config"
    config_path.write_text("Host existing\n    HostName example.com\n")

    result = write_ssh_config_entry("my-project", config_path)

    assert result.is_ok()
    content = config_path.read_text()
    assert "Host existing" in content
    assert "Host my-project" in content
    assert "ProxyCommand dvt ssh --stdio my-project" in content


def test_write_ssh_config_entry_is_idempotent(tmp_path):
    config_path = tmp_path / "config"
    write_ssh_config_entry("my-project", config_path)
    write_ssh_config_entry("my-project", config_path)
    assert config_path.read_text().count("Host my-project") == 1


def test_remove_ssh_config_entry_removes_block_only(tmp_path):
    config_path = tmp_path / "config"
    write_ssh_config_entry("keep-me", config_path)
    write_ssh_config_entry("remove-me", config_path)

    result = remove_ssh_config_entry("remove-me", config_path)

    assert result.is_ok()
    content = config_path.read_text()
    assert "Host keep-me" in content
    assert "Host remove-me" not in content


def test_remove_ssh_config_entry_noop_when_file_absent(tmp_path):
    result = remove_ssh_config_entry("anything", tmp_path / "nonexistent")
    assert result.is_ok()


def test_stdio_proxy_runs_real_ssh_server(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]
    captured = {}
    monkeypatch.setattr(
        ssh_module.ssh_server,
        "run_stdio_server",
        lambda cli_binary, container_name: captured.setdefault("args", (cli_binary, container_name)) or 0,
    )

    result = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert result.is_ok()
    assert result.unwrap() == 0
    assert captured["args"] == ("/usr/bin/docker", "dvt-my-project")


def test_stdio_proxy_returns_err_when_no_container_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    result = stdio_proxy("/usr/bin/docker", fake_client, "missing")

    assert result.is_err()


def test_stdio_proxy_returns_err_when_server_raises(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]
    monkeypatch.setattr(
        ssh_module.ssh_server,
        "run_stdio_server",
        lambda cli_binary, container_name: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert result.is_err()
```

Update the module-level import in `dvt/tests/test_ssh.py` to include
`from devtemplate import ssh as ssh_module` (for monkeypatching
`ssh_module.ssh_server`) alongside the existing direct imports.

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_ssh.py -v`
Expected: FAIL — `write_ssh_config_entry`/`remove_ssh_config_entry`/`stdio_proxy` don't exist in `ssh.py` yet (they were removed in the native runtime plan's final review).

- [ ] **Step 3: Implement**

Rewrite `dvt/src/devtemplate/ssh.py` in full:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate import ssh_server
from devtemplate.container import find_workspace_container

_BEGIN_MARKER = "# BEGIN dvt {name}"
_END_MARKER = "# END dvt {name}"


def write_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]:
    """Write/replace a `Host <name>` block whose ProxyCommand pipes through
    `dvt ssh --stdio <name>` - a real SSH server (ssh_server.py), not a bare
    shell. No sshd is ever installed into any image; the server runs entirely
    within the `dvt ssh --stdio` process itself."""
    try:
        removal = remove_ssh_config_entry(name, ssh_config_path)
        if removal.is_err():
            return removal
        block = (
            f"\n{_BEGIN_MARKER.format(name=name)}\n"
            f"Host {name}\n"
            f"    HostName {name}\n"
            f"    ProxyCommand dvt ssh --stdio {name}\n"
            f"    StrictHostKeyChecking no\n"
            f"    UserKnownHostsFile /dev/null\n"
            f"{_END_MARKER.format(name=name)}\n"
        )
        ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
        with ssh_config_path.open("a") as f:
            f.write(block)
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def remove_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]:
    if not ssh_config_path.exists():
        return Ok(None)
    try:
        begin, end = _BEGIN_MARKER.format(name=name), _END_MARKER.format(name=name)
        kept: list[str] = []
        skipping = False
        for line in ssh_config_path.read_text().splitlines(keepends=True):
            if line.strip() == begin:
                skipping = True
                continue
            if line.strip() == end:
                skipping = False
                continue
            if not skipping:
                kept.append(line)
        ssh_config_path.write_text("".join(kept))
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def stdio_proxy(cli_binary: str, client: DockerClient, name: str) -> Result[int, Exception]:
    """The non-interactive pipe mode `dvt ssh --stdio <name>` runs: finds the
    container labeled dvt.workspace=name and runs a real SSH server
    (ssh_server.run_stdio_server) bound to this process's own stdin/stdout,
    bridged to `docker/podman exec -i` in that container. This is what the
    ProxyCommand entry written by write_ssh_config_entry invokes."""
    try:
        container = find_workspace_container(client, name)
        if container is None or container.name is None:
            return Err(ValueError(f"No workspace named {name!r} is running."))
        return Ok(ssh_server.run_stdio_server(cli_binary, container.name))
    except Exception as exc:
        return Err(exc)


def exec_interactive(
    cli_binary: str, client: DockerClient, name: str
) -> Result[int, Exception]:
    """`dvt ssh <name>` typed directly at a terminal: finds the container labeled
    dvt.workspace=name and execs `docker exec -it` (inheriting this process's
    stdin/stdout/tty directly), returning its exit code. Unaffected by this
    plan - unlike stdio_proxy, this never involved SSH protocol at all."""
    try:
        container = find_workspace_container(client, name)
        if container is None or container.name is None:
            return Err(ValueError(f"No workspace named {name!r} is running."))
        result = subprocess.run([cli_binary, "exec", "-it", container.name, "sh"])
        return Ok(result.returncode)
    except Exception as exc:
        return Err(exc)
```

- [ ] **Step 4: Restore the `up_workspace`/`delete` call sites**

In `dvt/src/devtemplate/workspace.py`, add the import
`from devtemplate.ssh import write_ssh_config_entry` and a
`_refresh_ssh_config(name: str) -> Result[None, Exception]` helper:

```python
def _refresh_ssh_config(name: str) -> Result[None, Exception]:
    try:
        return write_ssh_config_entry(name, Path.home() / ".ssh" / "config")
    except Exception as exc:
        return Err(exc)
```

Call it at the end of both `_resume_existing` (after a successful start/no-op)
and at the end of `up_workspace`'s full-build path (after
`run_lifecycle_commands` succeeds, before the final `return Ok(container)`),
checking `.is_err()` and propagating exactly like every other step in this
function.

In `dvt/src/devtemplate/cli.py`, restore the `--stdio` flag on the `ssh`
command and the `remove_ssh_config_entry` call in `delete`:

```python
from devtemplate.ssh import exec_interactive, remove_ssh_config_entry, stdio_proxy
```

```python
@app.command()
def ssh(
    name: str,
    stdio: bool = typer.Option(  # noqa: B008
        False, "--stdio", help="Non-interactive pipe mode for ProxyCommand use.", hidden=True
    ),
) -> None:
    """ssh into a running workspace (or, with --stdio, pipe stdio for ProxyCommand)."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(get_client(settings.runtime), console)
    result = (
        stdio_proxy(handle.cli_binary, handle.client, name)
        if stdio
        else exec_interactive(handle.cli_binary, handle.client, name)
    )
    exit_code = unwrap_or_exit(result, console)
    raise typer.Exit(code=exit_code)
```

In `delete`, after the existing `container.remove(force=True)` try/except
block, add back:

```python
    unwrap_or_exit(
        remove_ssh_config_entry(name, Path.home() / ".ssh" / "config"), console
    )
```

Update `up`'s success message back to referencing real `ssh` (now that it
actually works):

```python
    console.print(f"[green]Workspace '{name}' is up.[/green] ssh in with: ssh {name}")
```

Update the corresponding tests in `dvt/tests/test_workspace.py` and
`dvt/tests/test_cli.py` (mock `write_ssh_config_entry`/`remove_ssh_config_entry`
the same way the native runtime plan's original Task 6/7 tests did — check
`git log` for those commits if you want the exact prior test code as a
reference, since this is a genuine restoration, not new design).

- [ ] **Step 5: Run to verify it passes, full suite + quality check**

Run: `pixi run -e dev test all`
Expected: all PASS.

Run: `pixi run -e dev quality check`
Expected: all Pass.

- [ ] **Step 6: Update docs**

In `dvt/docs/content/commands.md`, update the SSH section to reflect that
`dvt ssh --stdio` now runs a real SSH server, so plain `ssh <name>`, VS Code
Remote-SSH, and JetBrains Gateway are supported again through the
`~/.ssh/config` entry `dvt up` writes. Remove the "not supported" language
this plan's predecessor added.

- [ ] **Step 7: Commit**

```bash
git add dvt/src/devtemplate/ssh.py dvt/src/devtemplate/workspace.py dvt/src/devtemplate/cli.py dvt/tests/test_ssh.py dvt/tests/test_workspace.py dvt/tests/test_cli.py dvt/docs/content/commands.md
git commit -m "feat(dvt): restore ~/.ssh/config integration now backed by a real SSH server"
```

---

### Task 6: Real end-to-end integration test, JetBrains Gateway verification, final docs

**Files:**
- Modify: `dvt/tests/integration/test_native_runtime_lifecycle.py`
- Modify: `dvt/docs/content/concepts.md` (optional, if worth a note)

**Interfaces:**
- Consumes: the full `up` → `ssh` (via a real `ssh` binary, not `dvt ssh`) → `stop` → `delete` flow.

This is the test that would have caught the original ProxyCommand bug — it
must drive a *real* `ssh` client binary (not `asyncssh`, not `dvt ssh`
directly) through the actual `~/.ssh/config` entry `dvt up` writes, proving
the whole chain genuinely works end to end.

- [ ] **Step 1: Extend the real integration test**

In `dvt/tests/integration/test_native_runtime_lifecycle.py`, after the
existing `up_result` assertion and before `stop_result`, add:

```python
import shutil
import subprocess

...

    ssh_binary = shutil.which("ssh")
    if ssh_binary is not None:
        ssh_result = subprocess.run(
            [ssh_binary, "-F", str(Path.home() / ".ssh" / "config"), workspace_name, "echo hello-from-real-ssh"],
            capture_output=True, text=True, timeout=30,
        )
        assert ssh_result.returncode == 0, ssh_result.stderr
        assert "hello-from-real-ssh" in ssh_result.stdout
```

(Skip this block gracefully — not the whole test — if `ssh` isn't on `PATH`;
most CI runners and dev machines have it, but don't make the entire lifecycle
test depend on it.)

- [ ] **Step 2: Run it for real**

Run: `pixi run -e dev test integration`
Expected: PASS if a Docker or Podman engine (and the real `ssh` binary) is
reachable locally — this is the one test in the whole suite that proves the
SSH bridge genuinely works against a live container, not just a live asyncssh
client in the same process (Task 4's own test). If it fails, the bridge in
`ssh_server.py` has a real bug — do not consider this plan done until this
passes for real at least once.

- [ ] **Step 3: JetBrains Gateway manual verification (not automated)**

Manually verify (not part of the automated suite - record the outcome in
your task report): with a workspace up via `dvt up <name>`, open JetBrains
Gateway, add a new SSH connection using the `Host <name>` entry `dvt up`
wrote to `~/.ssh/config`, and confirm Gateway can connect and open a remote
project. If this doesn't work, report exactly what failed (Gateway may have
additional requirements beyond bare SSH protocol negotiation, e.g. expecting
`sftp` or a persistent connection beyond a single exec session) - do not
silently mark this "done" without actually trying it.

- [ ] **Step 4: Commit**

```bash
git add dvt/tests/integration/test_native_runtime_lifecycle.py
git commit -m "test(dvt): real ssh client through ~/.ssh/config proves the SSH bridge works end to end"
```
