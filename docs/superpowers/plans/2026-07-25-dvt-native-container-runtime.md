# dvt Native Container Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `dvt`'s `devpod`-passthrough `up`/`ssh`/`stop`/`delete` with a native implementation built on `docker-py`, working against either a Docker or a Podman engine, that builds real OCI-Feature-layered images and runs containers other devcontainer-aware tooling (VS Code's Dev Containers extension, `@devcontainers/cli`, `devpod` itself) can recognize and attach to.

**Architecture:** Five new modules (`runtime.py`, `features.py`, `build.py`, `container.py`, `ssh.py`) plus an orchestration module (`workspace.py`) that `cli.py`'s thin `up`/`ssh`/`stop`/`delete` commands call into. Every fallible function returns `Result[T, Exception]`, unwrapped at the CLI boundary via the existing `unwrap_or_exit()` helper. No separate `dvt`-side workspace registry — a `dvt.workspace=<name>` container label is the single source of truth for `ssh`/`stop`/`delete` lookup.

**Tech Stack:** `docker-py` (a single client works against both Docker's and Podman's Docker-API-compatible endpoints), `httpx` (already a dependency — used for the hand-rolled OCI Distribution client that pulls Feature artifacts, since those aren't runnable container images `docker-py`'s own `images.pull()` can fetch), `tarfile`, `subprocess` (only for the SSH exec plumbing — see Task 5).

Full design background: `docs/superpowers/specs/2026-07-25-dvt-native-container-runtime-design.md`.

**Amended after the final whole-branch review (all 8 tasks below already implemented and merged at that point):** the `ProxyCommand`-shim SSH design described throughout this plan and the design spec is broken and was removed. `ProxyCommand` only replaces the network transport a real `ssh` client uses — the client still performs actual SSH protocol negotiation (version banner, key exchange) over that pipe, which a bare `docker exec ... sh` cannot participate in. This was incorrectly believed to mirror `devpod`'s own approach; in fact `devpod` works because it runs its own lightweight SSH-speaking agent inside the container, which this design deliberately never has (the "no sshd" choice). Net effect: plain `ssh <name>`, VS Code Remote-SSH, and JetBrains Gateway can never work through the `~/.ssh/config` entry this design wrote, no matter how the shim code itself is fixed.

**Resolution (human decision):** drop the `~/.ssh/config` integration entirely. `dvt ssh <name>` (direct `docker`/`podman exec -it`, no real SSH protocol involved) remains the only supported terminal-access path — this already worked correctly and needs no change. Removed as dead/misleading: `write_ssh_config_entry`, `remove_ssh_config_entry`, `stdio_proxy`, the `ssh --stdio` CLI flag, and every doc/message claiming real-`ssh`/Remote-SSH/Gateway compatibility. Everywhere below that still describes or tests the `ProxyCommand`/`--stdio`/`write_ssh_config_entry` mechanism is superseded by this note, not authoritative — it's kept in place as a historical record of what was actually built and reviewed task-by-task, not as a spec to implement or re-implement.

## Global Constraints

- New dependency: `docker>=7.0`, added to `[project.dependencies]` in `dvt/pyproject.toml` exactly like every other runtime dependency there (not `[tool.pixi.dependencies]` — see how `httpx`/`typer`/etc. are already declared).
- New `Settings` field: `runtime: Literal["auto", "docker", "podman"] = "auto"`, env var `DVT_RUNTIME` (via the existing `env_prefix="DVT_"`).
- Every fallible function returns `Result[T, Exception]` — no bare exceptions escaping to callers, no `on_err`/retry decorators on anything in this plan (retries stay reserved for `github.py`'s GitHub API calls, per its own existing docstring rationale — a container build/run/exec failure is not a transient error to retry past).
- `devpod` is removed entirely by the end of this plan: no references in `src/`, `tests/`, or `docs/content/`.
- ~~SSH is a `ProxyCommand` shim over `docker exec` — no sshd is ever installed into any image, no port is ever published for SSH.~~ **Superseded, see amendment above:** `dvt ssh <name>` (direct `docker`/`podman exec -it`) is the only supported terminal-access path; no `~/.ssh/config` integration, no `--stdio` mode.
- Workspace lookup is always via the `dvt.workspace=<name>` container label (`docker ps --filter`) — never a `dvt`-side state file.
- A `devcontainer.json` using any out-of-scope spec field (`dockerComposeFile`, `build`, `onCreateCommand`/`updateContentCommand`/`initializeCommand`/`postAttachCommand`, per-Feature `installsAfter`/`dependsOn`) is refused before anything is built or run — never silently ignored.
- `requires-python = ">=3.12,<3.15"` is unchanged; `tarfile.extractall(..., filter="data")` (PEP 706, available since 3.12) is used explicitly for the Feature-artifact extraction rather than relying on the 3.12/3.13 default (which is still "fully trusted" unless a filter is passed).

---

### Task 1: Runtime resolution (`runtime.py`)

**Files:**
- Modify: `dvt/pyproject.toml` (add `docker` dependency)
- Modify: `dvt/src/devtemplate/config.py` (add `runtime` field)
- Create: `dvt/src/devtemplate/runtime.py`
- Test: `dvt/tests/test_runtime.py`
- Test: `dvt/tests/test_config.py` (add coverage for the new field)

**Interfaces:**
- Produces: `RuntimeHandle` (frozen dataclass: `client: DockerClient`, `engine: Literal["docker", "podman"]`, `cli_binary: str`) and `get_client(runtime: Literal["auto", "docker", "podman"]) -> Result[RuntimeHandle, Exception]`, both from `devtemplate.runtime`. `cli_binary` is the resolved path to the `docker`/`podman` executable, consumed by Task 5's SSH exec plumbing.
- Consumes: `devtemplate.config.Settings.runtime` (new field, this task).

- [ ] **Step 1: Add the `docker` dependency**

In `dvt/pyproject.toml`, add to `[project] dependencies`:

```toml
    "docker>=7.0",
```

- [ ] **Step 2: Add `Settings.runtime`**

In `dvt/src/devtemplate/config.py`, add `Literal` to the `typing` import and add the field:

```python
from typing import Literal, cast
```

```python
    runtime: Literal["auto", "docker", "podman"] = "auto"
```
(placed alongside `github_repo`/`github_branch`, before the `@field_validator`s — no validator needed since `Literal` already restricts pydantic to the three values.)

Add a test to `dvt/tests/test_config.py`:

```python
def test_runtime_defaults_to_auto(settings):
    assert settings.runtime == "auto"


def test_runtime_reads_from_env(monkeypatch, settings):
    monkeypatch.setenv("DVT_RUNTIME", "podman")
    from devtemplate.config import Settings

    assert Settings().runtime == "podman"
```

- [ ] **Step 2b: Run to verify it fails, then passes**

Run: `pixi run -e dev pytest tests/test_config.py -v`
Expected before Step 2's field is added: `AttributeError` / `AssertionError` on `settings.runtime`.
Expected after: both PASS.

- [ ] **Step 3: Write the failing tests for `runtime.py`**

Create `dvt/tests/test_runtime.py`:

```python
from __future__ import annotations

from devtemplate import runtime as runtime_module


class _FakeClient:
    def __init__(self, reachable: bool = True):
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise ConnectionError("not reachable")
        return True


def test_get_client_docker_success(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "docker" else None
    )
    monkeypatch.setattr(runtime_module.docker, "from_env", lambda: _FakeClient())

    result = runtime_module.get_client("docker")

    assert result.is_ok()
    handle = result.unwrap()
    assert handle.engine == "docker"
    assert handle.cli_binary == "/usr/bin/docker"


def test_get_client_docker_unreachable_is_err(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(runtime_module.docker, "from_env", lambda: _FakeClient(reachable=False))

    result = runtime_module.get_client("docker")

    assert result.is_err()


def test_get_client_docker_missing_binary_is_err(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: None)

    result = runtime_module.get_client("docker")

    assert result.is_err()


def test_get_client_podman_uses_container_host(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "podman" else None
    )
    monkeypatch.setenv("CONTAINER_HOST", "unix:///tmp/podman.sock")
    captured = {}

    def fake_docker_client(base_url):
        captured["base_url"] = base_url
        return _FakeClient()

    monkeypatch.setattr(runtime_module.docker, "DockerClient", fake_docker_client)

    result = runtime_module.get_client("podman")

    assert result.is_ok()
    assert captured["base_url"] == "unix:///tmp/podman.sock"
    assert result.unwrap().engine == "podman"


def test_get_client_auto_falls_back_to_podman(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("docker", "podman") else None,
    )
    monkeypatch.setattr(runtime_module.docker, "from_env", lambda: _FakeClient(reachable=False))
    monkeypatch.setenv("CONTAINER_HOST", "unix:///tmp/podman.sock")
    monkeypatch.setattr(runtime_module.docker, "DockerClient", lambda base_url: _FakeClient())

    result = runtime_module.get_client("auto")

    assert result.is_ok()
    assert result.unwrap().engine == "podman"


def test_get_client_auto_err_when_nothing_reachable(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: None)

    result = runtime_module.get_client("auto")

    assert result.is_err()
```

- [ ] **Step 4: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.runtime'`.

- [ ] **Step 5: Implement `runtime.py`**

Create `dvt/src/devtemplate/runtime.py`:

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


@dataclass(frozen=True)
class RuntimeHandle:
    """A resolved container runtime: a docker-py client plus which engine and CLI
    binary it talks to. cli_binary is only used by ssh.py's interactive exec
    plumbing, which shells out to the bundled docker/podman CLI rather than
    proxying raw stdio through docker-py's own exec/attach socket API - see
    ssh.py's module docstring."""

    client: DockerClient
    engine: Literal["docker", "podman"]
    cli_binary: str


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
    """Best-effort default rootless Podman socket path on Linux/macOS. Podman on
    Windows (WSL2-backed `podman machine`) isn't covered - CONTAINER_HOST must be
    set explicitly there. See the design spec's Known Gaps."""
    if sys.platform == "win32" or not hasattr(os, "getuid"):
        return None
    return f"unix:///run/user/{os.getuid()}/podman/podman.sock"


def _try_podman() -> RuntimeHandle | None:
    cli_binary = shutil.which("podman")
    if cli_binary is None:
        return None
    socket_url = os.environ.get("CONTAINER_HOST") or _default_podman_socket()
    if socket_url is None:
        return None
    try:
        client = docker.DockerClient(base_url=socket_url)
        client.ping()
    except Exception:
        return None
    return RuntimeHandle(client=client, engine="podman", cli_binary=cli_binary)


def get_client(
    runtime: Literal["auto", "docker", "podman"],
) -> Result[RuntimeHandle, Exception]:
    """Resolve a container runtime per Settings.runtime. "auto" tries Docker's
    endpoint first, then Podman's compatible socket."""
    if runtime == "docker":
        handle = _try_docker()
        if handle is None:
            return Err(RuntimeError("Docker not reachable (tried DOCKER_HOST / platform default)"))
        return Ok(handle)
    if runtime == "podman":
        handle = _try_podman()
        if handle is None:
            return Err(
                RuntimeError("Podman not reachable (tried CONTAINER_HOST / default rootless socket)")
            )
        return Ok(handle)
    handle = _try_docker() or _try_podman()
    if handle is None:
        return Err(RuntimeError("No container runtime found (tried Docker, Podman)"))
    return Ok(handle)
```

- [ ] **Step 6: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_runtime.py tests/test_config.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add dvt/pyproject.toml dvt/src/devtemplate/config.py dvt/src/devtemplate/runtime.py dvt/tests/test_runtime.py dvt/tests/test_config.py
git commit -m "feat(dvt): add native runtime resolution (Docker/Podman via docker-py)"
```

---

### Task 2: OCI Feature puller (`features.py`)

**Files:**
- Create: `dvt/src/devtemplate/features.py`
- Test: `dvt/tests/test_features.py`

**Interfaces:**
- Produces: `pull_feature(client: httpx.Client, ref: str, cache_dir: Path) -> Result[Path, Exception]` from `devtemplate.features` — returns the extracted Feature directory (containing at minimum `devcontainer-feature.json` and `install.sh`). Consumed by `workspace.py` in Task 6.
- Consumes: nothing new from earlier tasks (standalone, `httpx`-based).

This task's wire format was verified against a real registry before writing this
plan (`ghcr.io/jesserobertson/devcontainers/fastapi:latest`), not guessed from the
spec docs alone:
- Even anonymous/public pulls require a Bearer token: an unauthenticated manifest
  GET returns `401` with a `WWW-Authenticate: Bearer realm="...",service="...",scope="..."`
  header; GET the realm with those as query params to get `{"token": "..."}`.
- The manifest is `application/vnd.oci.image.manifest.v1+json` with one layer,
  `mediaType: "application/vnd.devcontainers.layer.v1+tar"`.
- The blob GET **redirects** (307, to a CDN URL) — `httpx` needs
  `follow_redirects=True`.
- The blob content is a **plain POSIX tar**, not gzip, despite the OCI
  annotation's `*.tgz` filename — extract with `tarfile.open(mode="r:")`.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_features.py`:

```python
from __future__ import annotations

import io
import tarfile

import httpx
import pytest

from devtemplate.features import _parse_feature_ref, pull_feature


def _make_feature_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in (
            ("devcontainer-feature.json", b'{"id": "fastapi", "version": "1.0.0"}'),
            ("install.sh", b"#!/bin/bash\necho installed\n"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _registry_handler(blob_digest: str, blob_bytes: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/manifests/latest") and "authorization" not in request.headers:
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",'
                        'service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith("/manifests/latest"):
            return httpx.Response(
                200,
                json={
                    "schemaVersion": 2,
                    "layers": [
                        {
                            "mediaType": "application/vnd.devcontainers.layer.v1+tar",
                            "digest": blob_digest,
                            "size": len(blob_bytes),
                        }
                    ],
                },
            )
        if path.endswith(f"/blobs/{blob_digest}"):
            return httpx.Response(200, content=blob_bytes)
        return httpx.Response(404)

    return handler


def test_parse_feature_ref_splits_registry_repository_tag():
    result = _parse_feature_ref("ghcr.io/jesserobertson/devcontainers/fastapi:latest")
    assert result.is_ok()
    assert result.unwrap() == ("ghcr.io", "jesserobertson/devcontainers/fastapi", "latest")


def test_parse_feature_ref_rejects_missing_tag():
    result = _parse_feature_ref("ghcr.io/jesserobertson/devcontainers/fastapi")
    assert result.is_err()


def test_pull_feature_extracts_devcontainer_feature_json_and_install_sh(tmp_path):
    blob = _make_feature_tar()
    handler = _registry_handler("sha256:deadbeef", blob)
    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_ok()
    extracted = result.unwrap()
    assert (extracted / "devcontainer-feature.json").exists()
    assert (extracted / "install.sh").read_text() == "#!/bin/bash\necho installed\n"


def test_pull_feature_is_cached_on_second_call(tmp_path):
    blob = _make_feature_tar()
    call_count = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return _registry_handler("sha256:deadbeef", blob)(request)

    client = httpx.Client(transport=httpx.MockTransport(counting_handler))
    ref = "ghcr.io/jesserobertson/devcontainers/fastapi:latest"

    first = pull_feature(client, ref, tmp_path)
    calls_after_first = call_count["n"]
    second = pull_feature(client, ref, tmp_path)

    assert first.is_ok() and second.is_ok()
    assert first.unwrap() == second.unwrap()
    assert call_count["n"] == calls_after_first  # no new HTTP calls on cache hit


def test_pull_feature_returns_err_on_manifest_404(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "authorization" not in request.headers and request.url.path.endswith("/manifests/latest"):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/missing:pull"'
                    )
                },
            )
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/missing:latest", tmp_path
    )

    assert result.is_err()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_features.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.features'`.

- [ ] **Step 3: Implement `features.py`**

Create `dvt/src/devtemplate/features.py`:

```python
from __future__ import annotations

import hashlib
import re
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from logerr import Err, Ok, Result

_WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')
_MANIFEST_ACCEPT = "application/vnd.oci.image.manifest.v1+json"


def _parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]:
    """Split 'registry/repository/path:tag' into (registry, repository_path, tag).

    E.g. 'ghcr.io/jesserobertson/devcontainers/fastapi:latest' ->
    ('ghcr.io', 'jesserobertson/devcontainers/fastapi', 'latest').
    """
    if "/" not in ref:
        return Err(ValueError(f"Invalid feature ref {ref!r}: expected registry/repository:tag"))
    registry, _, rest = ref.partition("/")
    if ":" not in rest:
        return Err(ValueError(f"Invalid feature ref {ref!r}: missing :tag"))
    repository, _, tag = rest.rpartition(":")
    if not repository or not tag:
        return Err(ValueError(f"Invalid feature ref {ref!r}: missing repository or tag"))
    return Ok((registry, repository, tag))


def _parse_www_authenticate(header_value: str) -> Result[dict[str, str], Exception]:
    params = dict(_WWW_AUTHENTICATE_PARAM.findall(header_value))
    if not {"realm", "service", "scope"} <= params.keys():
        return Err(ValueError(f"Unrecognized WWW-Authenticate header: {header_value!r}"))
    return Ok(params)


def _get_token(
    client: httpx.Client, registry: str, repository: str, tag: str
) -> Result[str, Exception]:
    """Anonymous OCI Distribution auth: probe the manifest endpoint unauthenticated,
    parse the resulting 401's WWW-Authenticate challenge, fetch a token from its
    realm. Registry-agnostic - not hardcoded to ghcr.io's own /token endpoint, since
    the realm/service/scope come from whatever registry actually answered."""
    probe = client.get(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={"Accept": _MANIFEST_ACCEPT},
    )
    if probe.status_code != 401:
        return Err(
            ValueError(f"Expected a 401 auth challenge from {registry}, got {probe.status_code}")
        )
    challenge = probe.headers.get("www-authenticate")
    if challenge is None:
        return Err(ValueError(f"401 response from {registry} had no WWW-Authenticate header"))
    params_result = _parse_www_authenticate(challenge)
    if params_result.is_err():
        return Err(params_result.unwrap_err())
    params = params_result.unwrap()
    try:
        token_response = client.get(
            params["realm"], params={"service": params["service"], "scope": params["scope"]}
        )
        token_response.raise_for_status()
        return Ok(token_response.json()["token"])
    except Exception as exc:
        return Err(exc)


def _fetch_manifest(
    client: httpx.Client, registry: str, repository: str, tag: str, token: str
) -> Result[dict[str, Any], Exception]:
    try:
        response = client.get(
            f"https://{registry}/v2/{repository}/manifests/{tag}",
            headers={"Accept": _MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return Ok(response.json())
    except Exception as exc:
        return Err(exc)


def _fetch_and_extract_layer(
    client: httpx.Client,
    registry: str,
    repository: str,
    digest: str,
    token: str,
    dest_dir: Path,
) -> Result[Path, Exception]:
    """Fetch a Feature's tar layer blob and extract it. Follows redirects - GHCR
    (and most registries) 307-redirects blob downloads to a CDN URL, unlike
    manifest/token requests which respond directly. The blob is a plain POSIX tar
    despite the OCI annotation's *.tgz filename - not gzip-compressed."""
    try:
        response = client.get(
            f"https://{registry}/v2/{repository}/blobs/{digest}",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        )
        response.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=BytesIO(response.content), mode="r:") as tar:
            tar.extractall(dest_dir, filter="data")
        return Ok(dest_dir)
    except Exception as exc:
        return Err(exc)


def pull_feature(client: httpx.Client, ref: str, cache_dir: Path) -> Result[Path, Exception]:
    """Pull and extract an OCI Feature artifact, returning its extracted directory.

    Cached under cache_dir / sha256(ref) - hashed rather than derived from the ref's
    own text, since a ref can contain arbitrary registry/path characters that aren't
    all safe path segments, and (unlike this repo's own template names) Feature refs
    aren't restricted to a known-safe pattern - they can point at any registry.
    """
    ref_result = _parse_feature_ref(ref)
    if ref_result.is_err():
        return Err(ref_result.unwrap_err())
    registry, repository, tag = ref_result.unwrap()

    dest_dir = cache_dir / hashlib.sha256(ref.encode()).hexdigest()
    if dest_dir.exists():
        return Ok(dest_dir)

    token_result = _get_token(client, registry, repository, tag)
    if token_result.is_err():
        return Err(token_result.unwrap_err())
    token = token_result.unwrap()

    manifest_result = _fetch_manifest(client, registry, repository, tag, token)
    if manifest_result.is_err():
        return Err(manifest_result.unwrap_err())
    manifest = manifest_result.unwrap()

    layers = manifest.get("layers", [])
    if not layers:
        return Err(ValueError(f"Feature manifest for {ref!r} has no layers"))

    return _fetch_and_extract_layer(client, registry, repository, layers[0]["digest"], token, dest_dir)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_features.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/features.py dvt/tests/test_features.py
git commit -m "feat(dvt): pull OCI Feature artifacts directly (no devcontainers CLI needed)"
```

---

### Task 3: Dockerfile generation and build (`build.py`)

**Files:**
- Create: `dvt/src/devtemplate/build.py`
- Test: `dvt/tests/test_build.py`

**Interfaces:**
- Produces: `generate_dockerfile(base_image: str, features: list[tuple[str, str, dict[str, str]]]) -> str` and `build_image(client: DockerClient, base_image: str, features: list[tuple[str, Path, dict[str, str]]], tag: str, scratch_dir: Path) -> Result[str, Exception]`, both from `devtemplate.build`. Consumed by `workspace.py` (Task 6).
- Consumes: `RuntimeHandle.client` (Task 1) as the `DockerClient` passed to `build_image`; extracted Feature directories from `pull_feature()` (Task 2).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_build.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from devtemplate.build import build_image, generate_dockerfile


def test_generate_dockerfile_no_features():
    content = generate_dockerfile("ghcr.io/jesserobertson/base-ubuntu:latest", [])
    assert content == (
        "FROM ghcr.io/jesserobertson/base-ubuntu:latest AS stage0\n"
        "FROM stage0 AS final\n"
    )


def test_generate_dockerfile_single_feature():
    content = generate_dockerfile(
        "ghcr.io/jesserobertson/base-ubuntu:latest",
        [("fastapi", "features/0-fastapi", {})],
    )
    assert "FROM ghcr.io/jesserobertson/base-ubuntu:latest AS stage0" in content
    assert "FROM stage0 AS feature-0-fastapi" in content
    assert "COPY features/0-fastapi/ /tmp/dvt-feature/" in content
    assert "/tmp/dvt-feature/install.sh" in content
    assert "FROM feature-0-fastapi AS final" in content


def test_generate_dockerfile_quotes_option_values_safely():
    content = generate_dockerfile(
        "base:latest",
        [("ollama", "features/0-ollama", {"model": "llama3.2; rm -rf /"})],
    )
    assert "MODEL='llama3.2; rm -rf /'" in content


def test_build_image_writes_dockerfile_and_copies_features(tmp_path):
    feature_dir = tmp_path / "extracted"
    feature_dir.mkdir()
    (feature_dir / "install.sh").write_text("#!/bin/bash\n")
    scratch_dir = tmp_path / "scratch"

    fake_client = MagicMock()
    fake_client.images.build.return_value = (MagicMock(), iter([]))

    result = build_image(
        fake_client,
        "base:latest",
        [("fastapi", feature_dir, {})],
        "dvt/my-project:latest",
        scratch_dir,
    )

    assert result.is_ok()
    assert result.unwrap() == "dvt/my-project:latest"
    assert (scratch_dir / "Dockerfile").exists()
    assert (scratch_dir / "features" / "0-fastapi" / "install.sh").exists()
    fake_client.images.build.assert_called_once()
    _, kwargs = fake_client.images.build.call_args
    assert kwargs["tag"] == "dvt/my-project:latest"
    assert kwargs["path"] == str(scratch_dir)


def test_build_image_returns_err_on_build_failure(tmp_path):
    fake_client = MagicMock()
    fake_client.images.build.side_effect = RuntimeError("build failed")

    result = build_image(fake_client, "base:latest", [], "dvt/x:latest", tmp_path / "scratch")

    assert result.is_err()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_build.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.build'`.

- [ ] **Step 3: Implement `build.py`**

Create `dvt/src/devtemplate/build.py`:

```python
from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result


def _dockerfile_stage_name(index: int, feature_id: str) -> str:
    return f"feature-{index}-{feature_id}"


def generate_dockerfile(
    base_image: str, features: list[tuple[str, str, dict[str, str]]]
) -> str:
    """Generate a multi-stage Dockerfile: base image, then one stage per Feature
    that COPYs in its extracted directory (already placed under the build context
    at the given context-relative dir by build_image) and runs install.sh with its
    resolved options as env vars plus the spec's standard _REMOTE_USER/
    _CONTAINER_USER vars.

    features: list of (feature_id, context_relative_dir, resolved_options), in the
    order they appear in devcontainer.json's "features" map.
    """
    lines = [f"FROM {base_image} AS stage0"]
    current_stage = "stage0"
    for index, (feature_id, context_dir, options) in enumerate(features):
        stage_name = _dockerfile_stage_name(index, feature_id)
        lines.append(f"FROM {current_stage} AS {stage_name}")
        lines.append(f"COPY {context_dir}/ /tmp/dvt-feature/")
        env_assignments = " ".join(
            f"{key.upper()}={shlex.quote(value)}" for key, value in options.items()
        )
        env_prefix = f"{env_assignments} " if env_assignments else ""
        lines.append(
            "RUN chmod +x /tmp/dvt-feature/install.sh && "
            f"_REMOTE_USER=dev _CONTAINER_USER=dev {env_prefix}"
            "/tmp/dvt-feature/install.sh && rm -rf /tmp/dvt-feature"
        )
        current_stage = stage_name
    lines.append(f"FROM {current_stage} AS final")
    return "\n".join(lines) + "\n"


def build_image(
    client: DockerClient,
    base_image: str,
    features: list[tuple[str, Path, dict[str, str]]],
    tag: str,
    scratch_dir: Path,
) -> Result[str, Exception]:
    """Assemble a build context under scratch_dir (copying each extracted Feature
    directory in), write the generated Dockerfile, and build it. features: list of
    (feature_id, extracted_dir, resolved_options)."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    context_features: list[tuple[str, str, dict[str, str]]] = []
    for index, (feature_id, extracted_dir, options) in enumerate(features):
        context_relative = f"features/{index}-{feature_id}"
        shutil.copytree(extracted_dir, scratch_dir / context_relative)
        context_features.append((feature_id, context_relative, options))

    dockerfile_content = generate_dockerfile(base_image, context_features)
    (scratch_dir / "Dockerfile").write_text(dockerfile_content)

    try:
        client.images.build(path=str(scratch_dir), tag=tag, rm=True)
        return Ok(tag)
    except Exception as exc:
        return Err(exc)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_build.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/build.py dvt/tests/test_build.py
git commit -m "feat(dvt): generate and build multi-stage Feature Dockerfiles"
```

---

### Task 4: Container lifecycle and labels (`container.py`)

**Files:**
- Create: `dvt/src/devtemplate/container.py`
- Test: `dvt/tests/test_container.py`

**Interfaces:**
- Produces (all from `devtemplate.container`):
  - `refuse_unsupported(config: dict[str, Any]) -> Result[None, Exception]`
  - `resolve_workspace(config: dict[str, Any], project_path: Path) -> tuple[str, str]`
  - `compute_labels(config: dict[str, Any], name: str, project_path: Path, config_file: Path) -> dict[str, str]`
  - `run_container(client: DockerClient, image: str, config: dict[str, Any], name: str, project_path: Path, config_file: Path) -> Result[Container, Exception]`
  - `run_lifecycle_commands(container: Container, config: dict[str, Any]) -> Result[None, Exception]`
  - `find_workspace_container(client: DockerClient, name: str) -> Container | None`
- Consumes: nothing new from earlier tasks directly (takes a `DockerClient` from Task 1's `RuntimeHandle.client`, and an already-built image tag from Task 3).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_container.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devtemplate.container import (
    compute_labels,
    find_workspace_container,
    refuse_unsupported,
    resolve_workspace,
    run_container,
    run_lifecycle_commands,
)

FASTAPI_CONFIG = {
    "name": "fastapi",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "workspaceFolder": "/workspace",
    "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
    "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
    "mounts": ["source=fastapi-pixi-cache,target=/home/dev/.cache/pixi,type=volume"],
    "postCreateCommand": "pixi install",
    "remoteUser": "dev",
}

AGENT_CONFIG = {
    "name": "agent",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
    "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
    "postCreateCommand": "pixi install",
    "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
    "remoteUser": "dev",
}


@pytest.mark.parametrize("config", [FASTAPI_CONFIG, AGENT_CONFIG])
def test_refuse_unsupported_allows_current_templates(config):
    assert refuse_unsupported(config).is_ok()


def test_refuse_unsupported_rejects_compose():
    result = refuse_unsupported({"dockerComposeFile": "docker-compose.yml"})
    assert result.is_err()


def test_refuse_unsupported_rejects_build_dockerfile():
    result = refuse_unsupported({"build": {"dockerfile": "Dockerfile"}})
    assert result.is_err()


@pytest.mark.parametrize(
    "field", ["onCreateCommand", "updateContentCommand", "initializeCommand", "postAttachCommand"]
)
def test_refuse_unsupported_rejects_unsupported_lifecycle_fields(field):
    result = refuse_unsupported({field: "echo hi"})
    assert result.is_err()


def test_refuse_unsupported_rejects_installs_after():
    config = {"features": {"ghcr.io/x/y:latest": {"installsAfter": ["ghcr.io/x/z:latest"]}}}
    result = refuse_unsupported(config)
    assert result.is_err()


def test_resolve_workspace_uses_explicit_fields(tmp_path):
    folder, mount = resolve_workspace(FASTAPI_CONFIG, tmp_path)
    assert folder == "/workspace"
    assert "target=/workspace" in mount


def test_resolve_workspace_defaults_when_absent(tmp_path):
    project = tmp_path / "my-project"
    project.mkdir()
    folder, mount = resolve_workspace({}, project)
    assert folder == "/workspaces/my-project"
    assert f"target={folder}" in mount
    assert "type=bind" in mount


def test_compute_labels_encodes_metadata(tmp_path):
    config_file = tmp_path / ".devcontainer" / "devcontainer.json"
    labels = compute_labels(FASTAPI_CONFIG, "my-project", tmp_path, config_file)
    assert labels["dvt.workspace"] == "my-project"
    assert labels["devcontainer.local_folder"] == str(tmp_path.resolve())
    assert labels["devcontainer.config_file"] == str(config_file.resolve())
    assert "devcontainer.metadata" in labels


def test_run_container_translates_cap_add(tmp_path):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_client.containers.run.return_value = fake_container

    result = run_container(
        fake_client, "dvt/agent:latest", AGENT_CONFIG, "agent", tmp_path, tmp_path / "devcontainer.json"
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert set(kwargs["cap_add"]) == {"NET_ADMIN", "NET_RAW"}


def test_run_container_translates_gpus_all(tmp_path):
    config = {**FASTAPI_CONFIG, "runArgs": ["--gpus", "all"]}
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    result = run_container(
        fake_client, "dvt/jax:latest", config, "jax", tmp_path, tmp_path / "devcontainer.json"
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert len(kwargs["device_requests"]) == 1


def test_run_container_rejects_unknown_run_arg(tmp_path):
    config = {**FASTAPI_CONFIG, "runArgs": ["--privileged"]}
    fake_client = MagicMock()

    result = run_container(
        fake_client, "dvt/x:latest", config, "x", tmp_path, tmp_path / "devcontainer.json"
    )

    assert result.is_err()


def test_run_lifecycle_commands_runs_in_order():
    calls = []
    fake_container = MagicMock()

    def fake_exec_run(cmd):
        calls.append(cmd[-1])
        return (0, b"ok")

    fake_container.exec_run.side_effect = fake_exec_run

    result = run_lifecycle_commands(fake_container, AGENT_CONFIG)

    assert result.is_ok()
    assert calls == ["pixi install", "sudo /usr/local/bin/init-firewall.sh"]


def test_run_lifecycle_commands_stops_on_failure():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (1, b"boom")

    result = run_lifecycle_commands(fake_container, FASTAPI_CONFIG)

    assert result.is_err()


def test_find_workspace_container_filters_by_label():
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_client.containers.list.return_value = [fake_container]

    found = find_workspace_container(fake_client, "my-project")

    assert found is fake_container
    fake_client.containers.list.assert_called_once_with(
        all=True, filters={"label": "dvt.workspace=my-project"}
    )


def test_find_workspace_container_returns_none_when_absent():
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    assert find_workspace_container(fake_client, "missing") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_container.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.container'`.

- [ ] **Step 3: Implement `container.py`**

Create `dvt/src/devtemplate/container.py`:

```python
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import docker.types
from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Err, Ok, Result

UNSUPPORTED_LIFECYCLE_FIELDS = {
    "onCreateCommand",
    "updateContentCommand",
    "initializeCommand",
    "postAttachCommand",
}
SUPPORTED_LIFECYCLE_ORDER = ["postCreateCommand", "postStartCommand"]


def refuse_unsupported(config: dict[str, Any]) -> Result[None, Exception]:
    """Refuse (Err, nothing built) if config uses spec surface this runtime
    doesn't implement: docker-compose, build.dockerfile, lifecycle commands other
    than postCreateCommand/postStartCommand, or per-Feature installsAfter/
    dependsOn. See the design spec's Non-Goals for why each is out for v1."""
    if "dockerComposeFile" in config:
        return Err(ValueError("dockerComposeFile devcontainers are not supported"))
    if "build" in config:
        return Err(
            ValueError('build.dockerfile devcontainers are not supported - use "image" instead')
        )
    used_unsupported = UNSUPPORTED_LIFECYCLE_FIELDS & config.keys()
    if used_unsupported:
        return Err(
            ValueError(
                f"Unsupported lifecycle command(s): {sorted(used_unsupported)} "
                "(only postCreateCommand/postStartCommand are supported)"
            )
        )
    for feature_ref, feature_options in config.get("features", {}).items():
        if isinstance(feature_options, dict) and (
            "installsAfter" in feature_options or "dependsOn" in feature_options
        ):
            return Err(
                ValueError(
                    f"Feature {feature_ref!r} uses installsAfter/dependsOn, "
                    "which this runtime doesn't support (single-Feature only)"
                )
            )
    return Ok(None)


def resolve_workspace(config: dict[str, Any], project_path: Path) -> tuple[str, str]:
    """Returns (workspace_folder, workspace_mount_spec), applying the spec's
    /workspaces/<folder-name> default when devcontainer.json doesn't set them."""
    default_folder = f"/workspaces/{project_path.resolve().name}"
    workspace_folder = config.get("workspaceFolder", default_folder)
    workspace_mount = config.get(
        "workspaceMount",
        f"source={project_path.resolve()},target={workspace_folder},type=bind,consistency=cached",
    )
    return workspace_folder, workspace_mount


def compute_labels(
    config: dict[str, Any], name: str, project_path: Path, config_file: Path
) -> dict[str, str]:
    """The label contract other devcontainer-aware tooling (VS Code's Dev
    Containers extension, @devcontainers/cli, devpod) uses to recognize and
    introspect a container dvt built."""
    metadata_json = json.dumps(config)
    return {
        "devcontainer.metadata": base64.b64encode(metadata_json.encode()).decode(),
        "devcontainer.local_folder": str(project_path.resolve()),
        "devcontainer.config_file": str(config_file.resolve()),
        "dvt.workspace": name,
    }


def _parse_mount(mount_spec: str) -> dict[str, dict[str, str]]:
    """Parse a devcontainer.json mount string ('source=...,target=...,type=...')
    into docker-py's {source: {"bind": target, "mode": "rw"}} volumes form."""
    parts = dict(item.split("=", 1) for item in mount_spec.split(",") if "=" in item)
    return {parts["source"]: {"bind": parts["target"], "mode": "rw"}}


def _translate_run_args(run_args: list[str]) -> Result[tuple[list[str], list[Any]], Exception]:
    """Translate devcontainer.json's runArgs into (cap_add list, device_requests
    list) for docker-py's containers.run(). Only --cap-add=X and the two-element
    ["--gpus", "all"] form are recognized (the only shapes this repo's templates
    use) - any other flag is refused rather than silently dropped."""
    cap_adds: list[str] = []
    device_requests: list[Any] = []
    index = 0
    while index < len(run_args):
        arg = run_args[index]
        if arg.startswith("--cap-add="):
            cap_adds.append(arg.split("=", 1)[1])
            index += 1
        elif arg == "--gpus" and index + 1 < len(run_args) and run_args[index + 1] == "all":
            device_requests.append(docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]]))
            index += 2
        else:
            return Err(ValueError(f"Unsupported runArgs entry {arg!r}"))
    return Ok((cap_adds, device_requests))


def run_container(
    client: DockerClient,
    image: str,
    config: dict[str, Any],
    name: str,
    project_path: Path,
    config_file: Path,
) -> Result[Container, Exception]:
    workspace_folder, workspace_mount = resolve_workspace(config, project_path)
    volumes: dict[str, dict[str, str]] = {}
    for mount_spec in [workspace_mount, *config.get("mounts", [])]:
        volumes.update(_parse_mount(mount_spec))

    run_args_result = _translate_run_args(config.get("runArgs", []))
    if run_args_result.is_err():
        return Err(run_args_result.unwrap_err())
    cap_adds, device_requests = run_args_result.unwrap()

    try:
        container = client.containers.run(
            image,
            detach=True,
            name=f"dvt-{name}",
            labels=compute_labels(config, name, project_path, config_file),
            volumes=volumes,
            working_dir=workspace_folder,
            environment=config.get("containerEnv", {}),
            user=config.get("remoteUser"),
            cap_add=cap_adds,
            device_requests=device_requests,
        )
        return Ok(container)
    except Exception as exc:
        return Err(exc)


def run_lifecycle_commands(container: Container, config: dict[str, Any]) -> Result[None, Exception]:
    for field in SUPPORTED_LIFECYCLE_ORDER:
        command = config.get(field)
        if command is None:
            continue
        shell_command = command if isinstance(command, str) else " && ".join(command)
        try:
            exit_code, output = container.exec_run(["sh", "-c", shell_command])
        except Exception as exc:
            return Err(exc)
        if exit_code != 0:
            return Err(
                RuntimeError(f"{field} failed (exit {exit_code}): {output.decode(errors='replace')}")
            )
    return Ok(None)


def find_workspace_container(client: DockerClient, name: str) -> Container | None:
    """Find the container tagged dvt.workspace=name - the sole source of truth for
    workspace lookup (no separate dvt-side registry; see the design spec)."""
    containers = client.containers.list(all=True, filters={"label": f"dvt.workspace={name}"})
    return containers[0] if containers else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_container.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/container.py dvt/tests/test_container.py
git commit -m "feat(dvt): container run/label/lifecycle logic, refuse out-of-scope configs"
```

---

### Task 5: SSH ProxyCommand shim (`ssh.py`)

**Files:**
- Create: `dvt/src/devtemplate/ssh.py`
- Test: `dvt/tests/test_ssh.py`

**Interfaces:**
- Produces (all from `devtemplate.ssh`):
  - `write_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]`
  - `remove_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]`
  - `stdio_proxy(cli_binary: str, client: DockerClient, name: str) -> Result[int, Exception]`
  - `exec_interactive(cli_binary: str, client: DockerClient, name: str) -> Result[int, Exception]`

**Amended after Task 5's review round:** the plan originally specified these two as returning a bare `int`, in tension with this plan's own Global Constraint that every fallible function returns `Result[T, Exception]`. Confirmed with the human: switch to `Result[int, Exception]` (`Err` on a `find_workspace_container` lookup failure or a `subprocess.run` launch failure; `Ok(returncode)` is the process's own exit code either way, matching the original `_run_devpod`'s design this replaces). Task 7's `cli.py` wiring below is written for the ORIGINAL bare-`int` signature — when implementing Task 7, unwrap these via `unwrap_or_exit()` like every other `Result`-returning call in `cli.py`, not by calling them directly as `int`.
- Consumes: `find_workspace_container` from `devtemplate.container` (Task 4); `RuntimeHandle.cli_binary` (Task 1) as the `cli_binary` argument.

No sshd is ever installed into any image, and no port is ever published. `stdio_proxy`/`exec_interactive` deliberately shell out to the bundled `docker`/`podman` CLI (already present alongside any Docker/Podman install, unlike the removed `devpod` binary) rather than proxying raw stdio through `docker-py`'s own exec/attach socket API, since inheriting the parent process's file descriptors directly via `subprocess.run` is simpler and more robust than manually pumping bytes between a raw socket and this process's stdin/stdout, especially cross-platform on Windows.

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_ssh.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from devtemplate import ssh as ssh_module
from devtemplate.ssh import (
    exec_interactive,
    remove_ssh_config_entry,
    stdio_proxy,
    write_ssh_config_entry,
)


def test_write_ssh_config_entry_adds_host_block(tmp_path):
    config_path = tmp_path / "config"
    config_path.write_text("Host existing\n    HostName example.com\n")

    result = write_ssh_config_entry("my-project", config_path)

    assert result.is_ok()
    content = config_path.read_text()
    assert "Host existing" in content
    assert "Host my-project" in content
    assert "ProxyCommand dvt ssh --stdio my-project" in content


def test_write_ssh_config_entry_creates_file_and_parents(tmp_path):
    config_path = tmp_path / "nested" / "config"

    result = write_ssh_config_entry("my-project", config_path)

    assert result.is_ok()
    assert config_path.exists()


def test_write_ssh_config_entry_is_idempotent(tmp_path):
    config_path = tmp_path / "config"

    write_ssh_config_entry("my-project", config_path)
    write_ssh_config_entry("my-project", config_path)

    content = config_path.read_text()
    assert content.count("Host my-project") == 1


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


def test_stdio_proxy_execs_docker_exec(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    exit_code = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert exit_code == 0
    assert captured["args"] == ["/usr/bin/docker", "exec", "-i", "dvt-my-project", "sh"]


def test_stdio_proxy_returns_1_when_no_container_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    exit_code = stdio_proxy("/usr/bin/docker", fake_client, "missing")

    assert exit_code == 1


def test_exec_interactive_uses_tty_flags(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}
    monkeypatch.setattr(
        ssh_module.subprocess,
        "run",
        lambda args: captured.setdefault("args", args) or MagicMock(returncode=0),
    )

    exec_interactive("/usr/bin/docker", fake_client, "my-project")

    assert captured["args"] == ["/usr/bin/docker", "exec", "-it", "dvt-my-project", "sh"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_ssh.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.ssh'`.

- [ ] **Step 3: Implement `ssh.py`**

Create `dvt/src/devtemplate/ssh.py`:

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate.container import find_workspace_container

_BEGIN_MARKER = "# BEGIN dvt {name}"
_END_MARKER = "# END dvt {name}"


def write_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]:
    """Write/replace a `Host <name>` block whose ProxyCommand pipes through
    `dvt ssh --stdio <name>` (docker/podman exec under the hood - see
    stdio_proxy). No sshd, no port, no host keys."""
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


def stdio_proxy(cli_binary: str, client: DockerClient, name: str) -> int:
    """The non-interactive pipe mode `dvt ssh --stdio <name>` runs: finds the
    container labeled dvt.workspace=name and execs `docker exec -i` (inheriting
    this process's stdin/stdout directly), returning its exit code. This is what
    the ProxyCommand entry written by write_ssh_config_entry invokes."""
    container = find_workspace_container(client, name)
    if container is None:
        print(f"No workspace named {name!r} is running.", file=sys.stderr)
        return 1
    result = subprocess.run([cli_binary, "exec", "-i", container.name, "sh"])
    return result.returncode


def exec_interactive(cli_binary: str, client: DockerClient, name: str) -> int:
    """`dvt ssh <name>` typed directly at a terminal - same as stdio_proxy but
    with a real TTY (-it instead of -i)."""
    container = find_workspace_container(client, name)
    if container is None:
        print(f"No workspace named {name!r} is running.", file=sys.stderr)
        return 1
    result = subprocess.run([cli_binary, "exec", "-it", container.name, "sh"])
    return result.returncode
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_ssh.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/ssh.py dvt/tests/test_ssh.py
git commit -m "feat(dvt): SSH ProxyCommand shim over docker/podman exec, no sshd needed"
```

---

### Task 6: `up` orchestration (`workspace.py`)

**Files:**
- Create: `dvt/src/devtemplate/workspace.py`
- Test: `dvt/tests/test_workspace.py`

**Interfaces:**
- Produces: `up_workspace(handle: RuntimeHandle, settings: Settings, name: str, project_path: Path) -> Result[Container, Exception]` from `devtemplate.workspace`. Consumed by `cli.py` (Task 7).
- Consumes: `RuntimeHandle` (Task 1), `pull_feature` (Task 2), `build_image` (Task 3), `refuse_unsupported`/`run_container`/`run_lifecycle_commands`/`find_workspace_container` (Task 4), `write_ssh_config_entry` (Task 5).

Handles the re-`up` case (a workspace with this name already exists): if its
container is stopped, start it rather than rebuilding; if already running,
just ensure the SSH config entry is current and return it. Only builds+runs
from scratch when no container with that `dvt.workspace` label exists yet —
this mirrors `devpod up`'s own idempotent behavior for the cases dvt supports
(no in-place devcontainer.json changes are picked up on re-`up` in v1; delete
and re-`up` for that).

- [ ] **Step 1: Write the failing tests**

Create `dvt/tests/test_workspace.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devtemplate import workspace as workspace_module
from devtemplate.runtime import RuntimeHandle
from devtemplate.workspace import up_workspace


@pytest.fixture
def project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
                "postCreateCommand": "pixi install",
            }
        )
    )
    return tmp_path


@pytest.fixture
def handle() -> RuntimeHandle:
    return RuntimeHandle(client=MagicMock(), engine="docker", cli_binary="/usr/bin/docker")


def test_up_workspace_errs_when_no_devcontainer_json(tmp_path, handle, settings):
    result = up_workspace(handle, settings, "my-project", tmp_path)
    assert result.is_err()


def test_up_workspace_full_build_and_run_sequence(project, handle, settings, monkeypatch):
    monkeypatch.setattr(workspace_module, "find_workspace_container", lambda client, name: None)
    monkeypatch.setattr(
        workspace_module, "pull_feature", lambda client, ref, cache_dir: workspace_module.Ok(Path("/extracted"))
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: workspace_module.Ok("dvt/fastapi:latest")
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: workspace_module.Ok(fake_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: workspace_module.Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: workspace_module.Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert result.unwrap() is fake_container


def test_up_workspace_refuses_unsupported_config(tmp_path, handle, settings):
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps({"dockerComposeFile": "x.yml"}))

    result = up_workspace(handle, settings, "x", tmp_path)

    assert result.is_err()


def test_up_workspace_starts_existing_stopped_container(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "exited"
    monkeypatch.setattr(workspace_module, "find_workspace_container", lambda client, name: existing)
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: workspace_module.Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_called_once()


def test_up_workspace_noop_when_already_running(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "running"
    monkeypatch.setattr(workspace_module, "find_workspace_container", lambda client, name: existing)
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: workspace_module.Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_not_called()
```

Add the `settings` fixture usage from `dvt/tests/conftest.py` (already present — no changes needed there).

- [ ] **Step 2: Run to verify it fails**

Run: `pixi run -e dev pytest tests/test_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.workspace'`.

- [ ] **Step 3: Implement `workspace.py`**

Create `dvt/src/devtemplate/workspace.py`:

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
from docker.models.containers import Container
from logerr import Err, Ok, Result
from logerr.itertools import traverse_result

from devtemplate.build import build_image
from devtemplate.config import Settings
from devtemplate.container import (
    find_workspace_container,
    refuse_unsupported,
    run_container,
    run_lifecycle_commands,
)
from devtemplate.features import pull_feature
from devtemplate.runtime import RuntimeHandle
from devtemplate.ssh import write_ssh_config_entry


def _feature_id(ref: str) -> str:
    """Derive a short id from an OCI ref's trailing path segment, e.g.
    'ghcr.io/jesserobertson/devcontainers/fastapi:latest' -> 'fastapi'. Used only
    for Dockerfile stage naming, not read from the Feature's own
    devcontainer-feature.json "id" field - an acceptable v1 simplification since
    this repo's own Features always keep the two in sync by construction."""
    return ref.rsplit("/", 1)[-1].split(":")[0]


def _load_config(config_file: Path) -> Result[dict[str, Any], Exception]:
    if not config_file.exists():
        return Err(FileNotFoundError(f"{config_file} not found. Run 'dvt project init' first."))
    try:
        return Ok(json.loads(config_file.read_text()))
    except Exception as exc:
        return Err(exc)


def up_workspace(
    handle: RuntimeHandle, settings: Settings, name: str, project_path: Path
) -> Result[Container, Exception]:
    """Full `dvt up` sequence: validate -> pull Features -> build -> run ->
    lifecycle commands -> SSH config. Returns the running Container. If a
    container already carries this workspace's label, starts it (if stopped) or
    returns it as-is (if already running) instead of rebuilding."""
    existing = find_workspace_container(handle.client, name)
    if existing is not None:
        if existing.status != "running":
            try:
                existing.start()
            except Exception as exc:
                return Err(exc)
        ssh_result = write_ssh_config_entry(name, Path.home() / ".ssh" / "config")
        if ssh_result.is_err():
            return Err(ssh_result.unwrap_err())
        return Ok(existing)

    config_file = project_path / ".devcontainer" / "devcontainer.json"
    config_result = _load_config(config_file)
    if config_result.is_err():
        return Err(config_result.unwrap_err())
    config = config_result.unwrap()

    refusal = refuse_unsupported(config)
    if refusal.is_err():
        return Err(refusal.unwrap_err())

    feature_refs = list(config.get("features", {}).keys())
    with httpx.Client() as http_client:
        pulled_result = traverse_result(
            feature_refs,
            lambda ref: pull_feature(http_client, ref, settings.data_dir / "features"),
        )
    if pulled_result.is_err():
        return Err(pulled_result.unwrap_err())

    features = [
        (_feature_id(ref), extracted_dir, config["features"][ref])
        for ref, extracted_dir in zip(feature_refs, pulled_result.unwrap(), strict=True)
    ]

    with tempfile.TemporaryDirectory() as scratch:
        build_result = build_image(
            handle.client, config["image"], features, f"dvt/{name}:latest", Path(scratch)
        )
    if build_result.is_err():
        return Err(build_result.unwrap_err())

    run_result = run_container(
        handle.client, build_result.unwrap(), config, name, project_path, config_file
    )
    if run_result.is_err():
        return Err(run_result.unwrap_err())
    container = run_result.unwrap()

    lifecycle_result = run_lifecycle_commands(container, config)
    if lifecycle_result.is_err():
        return Err(lifecycle_result.unwrap_err())

    ssh_result = write_ssh_config_entry(name, Path.home() / ".ssh" / "config")
    if ssh_result.is_err():
        return Err(ssh_result.unwrap_err())

    return Ok(container)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_workspace.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/workspace.py dvt/tests/test_workspace.py
git commit -m "feat(dvt): orchestrate the full up sequence (pull, build, run, lifecycle, ssh)"
```

---

### Task 7: CLI rewiring — remove devpod, wire in the native runtime

**Files:**
- Modify: `dvt/src/devtemplate/cli.py`
- Modify: `dvt/tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6 (`get_client`, `up_workspace`, `find_workspace_container`, `stdio_proxy`/`exec_interactive`, `remove_ssh_config_entry`).
- Produces: nothing new — this task only rewires `cli.py`'s existing `up`/`ssh`/`stop`/`delete` command signatures and bodies.

`up`'s signature changes from `up(name, extra_args)` (a `devpod` workspace-path-or-name plus passthrough flags) to `up(name)` (cwd's `.devcontainer/devcontainer.json`, `name` is just the tag) — matching `project add-feature`'s existing cwd-relative convention, per the design spec. `ssh` gains a hidden `--stdio` flag.

- [ ] **Step 1: Update the failing/changed tests first**

Replace the devpod-specific tests in `dvt/tests/test_cli.py` (`test_up_invokes_devpod_up`, `test_ssh_forwards_extra_args`, `test_stop_and_delete_invoke_devpod`, `test_devpod_not_on_path_reports_clean_error`, `test_devpod_launch_failure_reports_clean_error`) with:

```python
def test_up_builds_and_runs_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    fake_handle = object()
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        cli_module, "up_workspace", lambda handle, settings, name, path: cli_module.Ok(object())
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0


def test_up_reports_clean_error_on_failure(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(object())
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path: cli_module.Err(FileNotFoundError("no devcontainer.json")),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 1
    assert "devcontainer.json" in result.output


def test_ssh_interactive_execs_into_container(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(cli_module, "get_client", lambda runtime: cli_module.Ok(object()))
    monkeypatch.setattr(
        cli_module, "exec_interactive", lambda cli_binary, client, name: 0
    )

    result = runner.invoke(cli_module.app, ["ssh", "my-project"])

    assert result.exit_code == 0


def test_ssh_stdio_uses_stdio_proxy(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(cli_module, "get_client", lambda runtime: cli_module.Ok(object()))
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "stdio_proxy",
        lambda cli_binary, client, name: captured.setdefault("called", True) or 0,
    )

    result = runner.invoke(cli_module.app, ["ssh", "--stdio", "my-project"])

    assert result.exit_code == 0
    assert captured.get("called") is True


def test_stop_stops_the_labeled_container(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"stop": lambda self: None})()
    monkeypatch.setattr(cli_module, "get_client", lambda runtime: cli_module.Ok(object()))
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 0


def test_stop_reports_clean_error_when_not_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(cli_module, "get_client", lambda runtime: cli_module.Ok(object()))
    monkeypatch.setattr(cli_module, "find_workspace_container", lambda client, name: None)

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 1


def test_delete_removes_container_and_ssh_entry(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(cli_module, "get_client", lambda runtime: cli_module.Ok(object()))
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete", "my-project"])

    assert result.exit_code == 0
```

Keep `test_cli_help_exits_zero`, `test_template_subcommand_is_registered`, `test_project_subcommand_is_registered` unchanged.

- [ ] **Step 2: Run to verify the new tests fail**

Run: `pixi run -e dev pytest tests/test_cli.py -v`
Expected: FAIL — `cli_module` has no attributes `get_client`/`up_workspace`/etc. yet (still has the old `_run_devpod` shape).

- [ ] **Step 3: Rewrite `cli.py`**

Replace `dvt/src/devtemplate/cli.py` in full:

```python
from __future__ import annotations

from pathlib import Path

import typer
from logerr import Err, Ok, Result
from rich.console import Console
from rich.markup import escape

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.commands import project, template
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client
from devtemplate.ssh import exec_interactive, remove_ssh_config_entry, stdio_proxy
from devtemplate.workspace import up_workspace

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")
app.add_typer(template.app, name="template")
app.add_typer(project.app, name="project")
console = Console()

_SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"


@app.command()
def up(name: str) -> None:
    """Build and run a workspace from ./.devcontainer/devcontainer.json."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(get_client(settings.runtime), console)
    unwrap_or_exit(up_workspace(handle, settings, name, Path.cwd()), console)
    console.print(f"[green]Workspace '{name}' is up.[/green] ssh in with: ssh {name}")


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
    exit_code = (
        stdio_proxy(handle.cli_binary, handle.client, name)
        if stdio
        else exec_interactive(handle.cli_binary, handle.client, name)
    )
    raise typer.Exit(code=exit_code)


def _find_or_exit(client, name: str):
    container = find_workspace_container(client, name)
    if container is None:
        console.print(f"[red]No workspace named '{escape(name)}' found.[/red]")
        raise typer.Exit(code=1)
    return container


@app.command()
def stop(name: str) -> None:
    """Stop a running workspace."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(get_client(settings.runtime), console)
    container = _find_or_exit(handle.client, name)
    try:
        container.stop()
    except Exception as exc:
        console.print(f"[red]Failed to stop '{escape(name)}': {escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"Stopped '{name}'.")


@app.command()
def delete(name: str) -> None:
    """Delete a workspace's container (the built image is left cached)."""
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(get_client(settings.runtime), console)
    container = _find_or_exit(handle.client, name)
    try:
        container.remove(force=True)
    except Exception as exc:
        console.print(f"[red]Failed to delete '{escape(name)}': {escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    unwrap_or_exit(remove_ssh_config_entry(name, _SSH_CONFIG_PATH), console)
    console.print(f"Deleted '{name}'.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

Note `main()` drops the `logger.remove()`/`logerr.configure(enabled=False)` calls — **do not drop these**; keep them exactly as they were (they silence loguru/logerr's own console logging so dvt's Rich-formatted messages stay the only output). The block above omits them only for brevity in this plan; copy them from the current `cli.py` verbatim:

```python
def main() -> None:
    logger.remove()
    logerr.configure(enabled=False)
    app()
```

(restore the `import logerr`, `from loguru import logger` imports accordingly).

- [ ] **Step 4: Run to verify it passes**

Run: `pixi run -e dev pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Full-suite check**

Run: `pixi run -e dev test all`
Expected: all PASS (no other test file references `_run_devpod`/`_devpod_passthrough`).

Run: `pixi run -e dev quality check`
Expected: mypy/ruff/format all PASS.

- [ ] **Step 6: Commit**

```bash
git add dvt/src/devtemplate/cli.py dvt/tests/test_cli.py
git commit -m "feat(dvt): rewire up/ssh/stop/delete onto the native runtime, remove devpod"
```

---

### Task 8: Integration test and docs

**Files:**
- Delete: `dvt/tests/integration/test_devpod_lifecycle.py`
- Create: `dvt/tests/integration/test_native_runtime_lifecycle.py`
- Modify: `dvt/docs/content/installation.md`
- Modify: `dvt/docs/content/commands.md`
- Modify: `dvt/docs/content/concepts.md`
- Modify: `dvt/scripts/test.py` (docstring only — "real devpod" → "real Docker/Podman")

**Interfaces:**
- Consumes: the full `up`/`ssh`/`stop`/`delete` CLI surface from Task 7.

- [ ] **Step 1: Remove the old devpod integration test**

```bash
git rm dvt/tests/integration/test_devpod_lifecycle.py
```

- [ ] **Step 2: Write the new integration test**

Create `dvt/tests/integration/test_native_runtime_lifecycle.py`:

```python
"""Real container-runtime lifecycle integration test.

Opt-in only - run with `pixi run test integration`, never part of `pixi run test
all`, `pixi run pytest`, or CI. Requires a reachable Docker or Podman engine
(skips cleanly, not a failure, if neither is reachable).

Builds a real image (no Features, to keep the test fast and independent of
ghcr.io availability) from a minimal public base image, runs it, execs a command
via `dvt ssh --stdio`, then stops and deletes it - exercising the full native
runtime path with no mocking.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.runtime import get_client

runner = CliRunner()

pytestmark = pytest.mark.integration

runtime_unreachable = get_client("auto").is_err()


@pytest.fixture
def real_project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "dvt-integration-test", "image": "alpine:latest"})
    )
    return tmp_path


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_up_ssh_stop_delete_lifecycle(real_project: Path, monkeypatch) -> None:
    workspace_name = f"dvt-integration-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(real_project)

    try:
        up_result = runner.invoke(app, ["up", workspace_name])
        assert up_result.exit_code == 0, up_result.output

        ssh_result = runner.invoke(app, ["ssh", "--stdio", workspace_name], input="echo hi\nexit\n")
        assert ssh_result.exit_code == 0, ssh_result.output

        stop_result = runner.invoke(app, ["stop", workspace_name])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_name])
```

- [ ] **Step 3: Run it for real**

Run: `pixi run -e dev test integration`
Expected: PASS if a Docker or Podman engine is reachable locally (builds and runs a real `alpine:latest` container); SKIPPED with a clear reason otherwise. If it fails rather than skips, investigate before proceeding — this is the one test in the whole suite that proves the native runtime actually works end to end, not just against mocks.

- [ ] **Step 4: Update docs**

In `dvt/docs/content/installation.md`, replace the `DevPod` requirement bullet:

```markdown
- [DevPod](https://devpod.sh) and a container runtime (Docker Desktop, Podman, etc.) — only
  needed for the `up`/`ssh`/`stop`/`delete` commands. `template`/`project` commands work
  without either.
```

with:

```markdown
- [Docker](https://www.docker.com/) or [Podman](https://podman.io/) — only needed for the
  `up`/`ssh`/`stop`/`delete` commands. `template`/`project` commands work without either.
```

In `dvt/docs/content/commands.md`, replace the entire "Lifecycle passthroughs" section with:

```markdown
## Workspace lifecycle

`dvt up <name>` builds an image from cwd's `.devcontainer/devcontainer.json` — pulling
each referenced Feature as a real OCI artifact and baking it into a generated
multi-stage Dockerfile, exactly the way `@devcontainers/cli`/`devpod` themselves
build Features — then runs the container. `<name>` is the tag given to the
resulting workspace, not a path; run `up` from inside the project directory. If a
workspace with that name already exists, `up` starts it (if stopped) or leaves it
running (if already running) rather than rebuilding — delete and re-`up` to pick up
devcontainer.json changes.

`dvt ssh <name>` execs into the running container. Under the hood this is a
`ProxyCommand` shim over `docker exec`/`podman exec` — no SSH server is ever
installed into any image, no port is ever published. `dvt up` writes a
`~/.ssh/config` `Host <name>` entry the first time, so plain `ssh <name>`, VS Code
Remote-SSH, and JetBrains Gateway all work the same way `dvt ssh <name>` does.

`dvt stop <name>` / `dvt delete <name>` find the workspace via its `dvt.workspace`
container label — not a `dvt`-side registry — so they work from any directory.
`delete` also removes the workspace's `~/.ssh/config` entry, but leaves the built
image cached for a faster `up` next time.

These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `template`/`project` commands don't.
```

In `dvt/docs/content/concepts.md`, add this section (after the existing merge-semantics
content, before any closing summary):

```markdown
## Compatibility with other devcontainer tooling

Containers `dvt up` runs carry the same labels other devcontainer-aware tooling
looks for — `devcontainer.metadata` (base64-encoded JSON of the merged config),
`devcontainer.local_folder`, and `devcontainer.config_file` — plus `dvt.workspace`,
the label `ssh`/`stop`/`delete` filter on. This means VS Code's own "Attach to
Running Container" command (part of the Dev Containers extension, no `devpod`
needed) recognizes and can introspect a workspace `dvt` built, and the images `dvt`
builds are normal, standalone images usable by anything with a Docker or Podman
client — not `dvt`-specific artifacts.

This is compatibility, not full spec parity. `dvt` does not implement:

- **docker-compose devcontainers** (`dockerComposeFile`) — image-only devcontainer.json
- **Feature dependency ordering** (`installsAfter`/`dependsOn`) — one Feature per
  devcontainer.json is assumed
- **`build.dockerfile`-based devcontainer.json** — use `image` instead
- **`onCreateCommand`/`updateContentCommand`/`initializeCommand`/`postAttachCommand`**
  — only `postCreateCommand` and `postStartCommand` run. `initializeCommand` in
  particular runs on the *host* in the real spec, before the container exists; `dvt`
  refuses it outright rather than running it in the wrong place.

A `devcontainer.json` using any of these is refused at `up` time — nothing is built
or run — rather than silently doing something different from what it asks for.
```

In `dvt/scripts/test.py`, update the `integration` command's docstring: replace "real devpod/network calls" with "real Docker/Podman/network calls".

- [ ] **Step 5: Full-suite check**

Run: `pixi run -e dev quality check && pixi run -e dev docs build --strict`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add dvt/tests/integration/test_native_runtime_lifecycle.py dvt/docs/content/installation.md dvt/docs/content/commands.md dvt/docs/content/concepts.md dvt/scripts/test.py
git commit -m "test(dvt): replace devpod integration test with native runtime lifecycle test, update docs"
```
