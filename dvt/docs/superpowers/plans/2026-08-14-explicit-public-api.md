# Explicit Public API via `__all__` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace underscore-prefix-as-privacy with explicit `__all__` declarations across every module in `src/devtemplate`, backed by a real mypy check at package boundaries, and split the three files an earlier audit found genuinely too dense (`ssh_server.py`, `features.py`, `workspace.py`+`workspace_lookup.py`) into purpose-named packages.

**Architecture:** Every module gets `__all__`; every package (new and existing) gets a populated `__init__.py` re-exporting its public surface; `[tool.mypy]` gains `implicit_reexport = false` so an un-declared re-export becomes a real `quality check` failure, not just an unenforced convention.

**Tech Stack:** Python 3.12+, existing project tooling only (ruff, mypy) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-14-explicit-public-api-design.md`

## Global Constraints

- No underscore-prefixed names at module level anywhere in `src/devtemplate` once this plan is done. A name's privacy is signaled solely by omission from its module's `__all__`.
- Every cross-module import that currently reaches a renamed name must be updated in the same task as the rename — a missed call site is a real `ImportError`, not a lint nit. Each task's steps include an explicit "grep for the old name, confirm zero remaining references" check.
- Every package's `__init__.py` (new: `sshd/`, `features/`, `workspace/`; existing: `commands/`, `pty/`) imports and `__all__`-lists its public surface. `schemas/` is the one exception — confirmed to hold no Python code at all (only a vendored JSON schema accessed via `importlib.resources`), so it needs no `__all__` and no changes.
- All imports, everywhere, stay full absolute dotted paths (`from devtemplate.workspace.existing import resume_existing`) — this codebase has zero relative imports today and this plan doesn't introduce the first one.
- Tests that currently import an underscore name for white-box testing keep importing directly from the defining submodule after the rename (not through a package's re-export) — e.g. `tests/test_sshd.py` imports `from devtemplate.sshd.session import handle_process`, not `from devtemplate.sshd import handle_process`.
- Every task runs the full suite (`pixi run test unit`) and the quality gate (`pixi run -e dev quality check`) before committing. Both must be clean — the quality gate now includes the `implicit_reexport = false` check from Task 1 onward.
- `CHANNEL_EVENTS` is **not** shared between `devtemplate.pty.bridge` and `devtemplate.sshd.session` despite similar names — they are genuinely different tuples for a real reason (the non-pty path has no pty to resize, so it lumps `TerminalSizeChanged` in with the generic ignore-and-continue set; the pty path handles resize specially and excludes it from its own `CHANNEL_EVENTS`). Only `CHUNK` and the drain-timeout constant are true duplicates and get shared. Do not merge `CHANNEL_EVENTS` into one constant in Task 3 or Task 4.

---

## Task 1: Enable `implicit_reexport = false`

**Files:**
- Modify: `pyproject.toml:85-94` (`[tool.mypy]`)

**Interfaces:**
- Produces: the enforcement mechanism every later task's package `__init__.py` relies on. No code interface — a build-time check only.

- [ ] **Step 1: Add the setting**

In `pyproject.toml`, the current `[tool.mypy]` section reads:

```toml
[tool.mypy]
python_version = "3.12"
plugins = ["pydantic.mypy"]
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_return_any = true
```

Add `implicit_reexport = false` as a new line anywhere in that block, e.g.:

```toml
[tool.mypy]
python_version = "3.12"
plugins = ["pydantic.mypy"]
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_return_any = true
implicit_reexport = false
```

- [ ] **Step 2: Run the quality gate to confirm no immediate fallout**

Run: `pixi run -e dev quality check`
Expected: still clean. Verified during spec research that no package `__init__.py` in this codebase currently does any implicit re-export (`commands/__init__.py`, `pty/__init__.py`, and `schemas/__init__.py` are all genuinely empty today), so this flag has nothing to catch yet — it only matters once later tasks populate those files. If this step is NOT clean, stop and investigate before proceeding; it means an implicit re-export exists somewhere this plan's research missed.

- [ ] **Step 3: Run the full suite**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this change (mypy config doesn't affect runtime behavior).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build(dvt): require explicit re-exports at package boundaries

implicit_reexport = false means a package __init__.py must list a
name in __all__ (or alias it 'as name') for other code to import it
through the package - enforced by the existing quality-check gate,
not just documented as a convention."
```

---

## Task 2: `devtemplate.features` package

**Files:**
- Create: `src/devtemplate/features/__init__.py`
- Create: `src/devtemplate/features/pull.py`
- Create: `src/devtemplate/features/oci.py`
- Delete: `src/devtemplate/features.py`
- Modify: `tests/test_features.py`

**Interfaces:**
- Produces: `devtemplate.features.pull_feature` (package-level, unchanged signature: `pull_feature(client: httpx.Client, ref: str, cache_dir: Path) -> Path`), `devtemplate.features.oci.parse_feature_ref` (renamed from `_parse_feature_ref`, same signature: `parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]`).
- Consumes: nothing new from other tasks.

- [ ] **Step 1: Confirm current external consumers**

Run: `grep -rn "from devtemplate.features import\|from devtemplate import features\|devtemplate\.features\." src/ tests/`
Expected output (confirmed during plan research, re-verify it hasn't changed):
- `src/devtemplate/workspace.py:26`: `from devtemplate.features import pull_feature`
- `tests/test_features.py:9`: `from devtemplate.features import _parse_feature_ref, pull_feature`

- [ ] **Step 2: Create `src/devtemplate/features/oci.py`**

This is `features.py`'s current lines 1-165 (everything except `pull_feature` itself), with every function renamed to drop its leading underscore, and `__all__` added listing all six:

```python
from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from logerr import Err, Ok, Result
from logerr.utilities import wrap_result

__all__ = [
    "parse_feature_ref",
    "parse_www_authenticate",
    "get_token",
    "fetch_manifest",
    "first_layer_digest",
    "fetch_and_extract_layer",
]

_WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')
_MANIFEST_ACCEPT = "application/vnd.oci.image.manifest.v1+json"


def parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]:
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


def parse_www_authenticate(header_value: str) -> Result[dict[str, str], Exception]:
    params = dict(_WWW_AUTHENTICATE_PARAM.findall(header_value))
    return Result.from_predicate(
        params,
        lambda p: {"realm", "service", "scope"} <= p.keys(),
        ValueError(f"Unrecognized WWW-Authenticate header: {header_value!r}"),
    )


@wrap_result
def get_token(client: httpx.Client, registry: str, repository: str, tag: str) -> str:
    """Anonymous OCI Distribution auth: probe the manifest endpoint unauthenticated,
    parse the resulting 401's WWW-Authenticate challenge, fetch a token from its
    realm. Registry-agnostic - not hardcoded to ghcr.io's own /token endpoint, since
    the realm/service/scope come from whatever registry actually answered."""
    probe = client.get(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={"Accept": _MANIFEST_ACCEPT},
    )
    if probe.status_code != 401:
        raise ValueError(
            f"Expected a 401 auth challenge from {registry}, got {probe.status_code}"
        )
    challenge = probe.headers.get("www-authenticate")
    if challenge is None:
        raise ValueError(f"401 response from {registry} had no WWW-Authenticate header")
    params = parse_www_authenticate(challenge).unwrap()
    token_response = client.get(
        params["realm"],
        params={"service": params["service"], "scope": params["scope"]},
    )
    token_response.raise_for_status()
    return str(token_response.json()["token"])


def first_layer_digest(manifest: Any, ref: str) -> Result[str, Exception]:
    """Pull the first layer's digest out of a manifest, treating any malformed
    shape (a non-dict manifest, no layers, a non-dict layer entry, a
    missing/non-string digest) as a Result error rather than letting
    AttributeError/KeyError/TypeError escape to the caller."""
    if not isinstance(manifest, dict):
        return Err(
            ValueError(
                f"Feature manifest for {ref!r} is not a JSON object: {manifest!r}"
            )
        )
    layers = manifest.get("layers", [])
    if not layers:
        return Err(ValueError(f"Feature manifest for {ref!r} has no layers"))
    layer = layers[0]
    if not isinstance(layer, dict) or not isinstance(layer.get("digest"), str):
        return Err(
            ValueError(
                f"Feature manifest for {ref!r} has a malformed first layer "
                f"(missing or non-string digest): {layer!r}"
            )
        )
    return Ok(layer["digest"])


@wrap_result
def fetch_manifest(
    client: httpx.Client, registry: str, repository: str, tag: str, token: str
) -> Any:
    """Returns whatever JSON value the registry responded with - not
    necessarily a dict. first_layer_digest is responsible for rejecting a
    non-dict manifest as a Result error rather than trusting this shape."""
    response = client.get(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={"Accept": _MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


@wrap_result
def fetch_and_extract_layer(
    client: httpx.Client,
    registry: str,
    repository: str,
    digest: str,
    token: str,
    dest_dir: Path,
) -> Path:
    """Fetch a Feature's tar layer blob and extract it. Follows redirects - GHCR
    (and most registries) 307-redirects blob downloads to a CDN URL, unlike
    manifest/token requests which respond directly. The blob is a plain POSIX tar
    despite the OCI annotation's *.tgz filename - not gzip-compressed.

    Extracts into a temporary sibling directory first and only renames it into
    place at dest_dir once extraction fully succeeds. This way a partial or
    corrupt extraction (bad tar, or extractall's filter="data" safety check
    rejecting a hostile archive) never leaves anything at dest_dir - so a later
    call for the same ref can't mistake a poisoned partial extraction for a
    valid cache hit.
    """
    response = client.get(
        f"https://{registry}/v2/{repository}/blobs/{digest}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
    )
    response.raise_for_status()
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=dest_dir.parent))
    try:
        with tarfile.open(fileobj=BytesIO(response.content), mode="r:") as tar:
            tar.extractall(tmp_dir, filter="data")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    tmp_dir.rename(dest_dir)
    return dest_dir
```

- [ ] **Step 3: Create `src/devtemplate/features/pull.py`**

```python
from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from logerr.utilities import wrap_result

from devtemplate.features.oci import (
    fetch_and_extract_layer,
    fetch_manifest,
    first_layer_digest,
    get_token,
    parse_feature_ref,
)

__all__ = ["pull_feature"]


@wrap_result
def pull_feature(client: httpx.Client, ref: str, cache_dir: Path) -> Path:
    """Pull and extract an OCI Feature artifact, returning its extracted directory.

    Cached under cache_dir / sha256(ref) - hashed rather than derived from the ref's
    own text, since a ref can contain arbitrary registry/path characters that aren't
    all safe path segments, and (unlike this repo's own template names) Feature refs
    aren't restricted to a known-safe pattern - they can point at any registry.
    """
    registry, repository, tag = parse_feature_ref(ref).unwrap()

    dest_dir = cache_dir / hashlib.sha256(ref.encode()).hexdigest()
    if dest_dir.exists():
        return dest_dir

    token = get_token(client, registry, repository, tag).unwrap()
    manifest = fetch_manifest(client, registry, repository, tag, token).unwrap()
    digest = first_layer_digest(manifest, ref).unwrap()

    return fetch_and_extract_layer(
        client, registry, repository, digest, token, dest_dir
    ).unwrap()
```

- [ ] **Step 4: Create `src/devtemplate/features/__init__.py`**

```python
from devtemplate.features.pull import pull_feature

__all__ = ["pull_feature"]
```

- [ ] **Step 5: Delete the old flat module**

```bash
git rm src/devtemplate/features.py
```

- [ ] **Step 6: Run the test suite to see the expected failure**

Run: `pixi run pytest tests/test_features.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_feature_ref' from 'devtemplate.features'` (the package's `__init__.py` no longer has that name - correct, it was never meant to be re-exported at the package level).

- [ ] **Step 7: Update `tests/test_features.py`**

Change line 9 from:

```python
from devtemplate.features import _parse_feature_ref, pull_feature
```

to:

```python
from devtemplate.features import pull_feature
from devtemplate.features.oci import parse_feature_ref
```

Then update every use of `_parse_feature_ref` in the rest of that file to `parse_feature_ref` (grep the file for `_parse_feature_ref` to find all call sites).

- [ ] **Step 8: Run tests to verify they pass**

Run: `pixi run pytest tests/test_features.py -v`
Expected: all tests pass.

Run: `grep -rn "_parse_feature_ref\|from devtemplate.features import" src/ tests/`
Expected: no remaining references to `_parse_feature_ref` anywhere; the only `from devtemplate.features import` lines are `pull_feature` (in `workspace.py` and `test_features.py`).

- [ ] **Step 9: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/devtemplate/features src/devtemplate/features.py tests/test_features.py
git commit -m "refactor(dvt): split features.py into a features/ package

Six OCI-registry-client helpers were hiding behind underscores in a
file with one real caller (pull_feature). Split into oci.py (the
reusable OCI client, now genuinely public within the package) and
pull.py (the one thing this package exports)."
```

---

## Task 3: `devtemplate.pty` package gains `__all__` everywhere + shared constants

**Files:**
- Modify: `src/devtemplate/pty/spawn.py`
- Modify: `src/devtemplate/pty/posix.py`
- Modify: `src/devtemplate/pty/windows.py`
- Modify: `src/devtemplate/pty/bridge.py`
- Modify: `src/devtemplate/pty/__init__.py`

**Interfaces:**
- Produces: `devtemplate.pty.spawn_pty_process` (unchanged signature, now also package-level), `devtemplate.pty.bridge_to_ssh_process` (unchanged signature, now also package-level), `devtemplate.pty.CHUNK` (`int`, `4096`), `devtemplate.pty.DRAIN_TIMEOUT` (`float`, `5.0`) — both new package-level constants, moved here from what will become `sshd/`'s duplicate definitions in Task 4.
- Consumes: nothing new from other tasks.

- [ ] **Step 1: Add `__all__` to `spawn.py`**

In `src/devtemplate/pty/spawn.py`, add after the imports (after `from typing import Protocol`, before `class PtyProcess`):

```python
__all__ = ["PtyProcess", "spawn_pty_process"]
```

No renames needed - `PtyProcess` and `spawn_pty_process` already have no underscore prefix.

- [ ] **Step 2: Add `__all__` to `posix.py` and `windows.py`**

In `src/devtemplate/pty/posix.py`, add near the top (after imports):

```python
__all__ = ["PosixPtyProcess", "spawn"]
```

In `src/devtemplate/pty/windows.py`, add near the top (after imports):

```python
__all__ = ["WindowsPtyProcess", "spawn"]
```

No renames needed in either file - confirmed via the plan's research grep that neither file has any underscore-prefixed module-level name.

- [ ] **Step 3: Move `CHUNK` and `DRAIN_TIMEOUT` into `bridge.py`, add `__all__`**

In `src/devtemplate/pty/bridge.py`, the current top of the file (after the module docstring) is:

```python
from __future__ import annotations

import asyncio
import codecs
import contextlib
import socket
import threading
from typing import TYPE_CHECKING

import asyncssh

if TYPE_CHECKING:
    from devtemplate.pty.spawn import PtyProcess

_CHUNK = 4096
_DRAIN_TIMEOUT = 5.0
"""Same rationale as ssh_server.py's _BRIDGE_DRAIN_TIMEOUT: how long to wait
for the blocking pump threads to notice their socket end closed and flush
their last output, after the async side has finished. The threads are
daemons, so exceeding it costs nothing beyond the lost output tail."""

_CHANNEL_EVENTS = (asyncssh.misc.BreakReceived, asyncssh.misc.SignalReceived)
"""Explicit SSH protocol-level signal/break requests - distinct from a
client typing Ctrl-C, which arrives as an ordinary 0x03 byte in the data
stream and needs no special handling here: it flows straight through to the
container's own pty (allocated by -t) exactly like dvt ssh <name>'s
already-working direct-exec path. Mirrors ssh_server.py's own
_CHANNEL_EVENTS handling for these two, minus TerminalSizeChanged, which
this bridge handles specially (see pump_client_to_pty below) rather than
ignoring."""
```

Replace it with (renaming `CHUNK`/`DRAIN_TIMEOUT` to drop their underscores and become this module's shared, exported constants; `CHANNEL_EVENTS` also drops its underscore but is **not** shared - see the Global Constraints note on why):

```python
from __future__ import annotations

import asyncio
import codecs
import contextlib
import socket
import threading
from typing import TYPE_CHECKING

import asyncssh

if TYPE_CHECKING:
    from devtemplate.pty.spawn import PtyProcess

__all__ = ["bridge_to_ssh_process", "CHUNK", "DRAIN_TIMEOUT"]

CHUNK = 4096
"""Read size for every byte pump touching a pty session - shared with
devtemplate.sshd, whose plain-pipe session path uses the identical value
for the identical reason (interactive terminal traffic, latency over
throughput)."""

DRAIN_TIMEOUT = 5.0
"""How long to wait for a blocking pump thread to notice its socket end
closed and flush its last output, after the async side has finished.
Shared with devtemplate.sshd for the same reason as CHUNK above - both
packages bridge blocking OS-level I/O into asyncio via a socketpair-plus-
daemon-thread shape, and both need the same drain budget."""

CHANNEL_EVENTS = (asyncssh.misc.BreakReceived, asyncssh.misc.SignalReceived)
"""Explicit SSH protocol-level signal/break requests - distinct from a
client typing Ctrl-C, which arrives as an ordinary 0x03 byte in the data
stream and needs no special handling here: it flows straight through to the
container's own pty (allocated by -t) exactly like dvt ssh <name>'s
already-working direct-exec path. NOT shared with devtemplate.sshd.session's
own CHANNEL_EVENTS despite the similar name and purpose - that one also
includes TerminalSizeChanged (harmless to ignore on the plain-pipe path,
which has no pty to resize), while this one deliberately excludes it
because bridge_to_ssh_process below handles resize specially rather than
ignoring it."""
```

Then update every use of `_CHUNK`, `_DRAIN_TIMEOUT`, `_CHANNEL_EVENTS` later in the same file to the new names without the leading underscore (`CHUNK`, `DRAIN_TIMEOUT`, `CHANNEL_EVENTS`) - there are 6 such uses across `pump_pty_to_socket`, `pump_socket_to_pty`, and `bridge_to_ssh_process`. Also rename the two private pump functions, dropping their underscores: `_pump_pty_to_socket` → `pump_pty_to_socket`, `_pump_socket_to_pty` → `pump_socket_to_pty` (these stay internal to this module - not in `__all__` - but the Naming convention applies to every module-level name regardless of `__all__` membership). Update the two `threading.Thread(target=...)` call sites inside `bridge_to_ssh_process` that reference these by name.

- [ ] **Step 4: Populate `src/devtemplate/pty/__init__.py`**

Currently empty (0 bytes). Replace with:

```python
from devtemplate.pty.bridge import CHUNK, DRAIN_TIMEOUT, bridge_to_ssh_process
from devtemplate.pty.spawn import spawn_pty_process

__all__ = ["spawn_pty_process", "bridge_to_ssh_process", "CHUNK", "DRAIN_TIMEOUT"]
```

- [ ] **Step 5: Run the pty test suite**

Run: `pixi run pytest tests/test_pty_spawn.py tests/test_pty_bridge.py -v --no-cov`
Expected: same pass/skip counts as before this task (no behavior change, only names and `__all__`). Windows tests should genuinely pass on this machine; POSIX tests skip here as always.

- [ ] **Step 6: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same counts as before, 0 new failures. `ssh_server.py` still imports `bridge_to_ssh_process`/`spawn_pty_process` via `from devtemplate.pty.bridge import ...`/`from devtemplate.pty.spawn import ...` (unchanged submodule paths, both still valid) - Task 4 is what switches those imports to the package level and moves this file to `sshd/`.

Run: `pixi run -e dev quality check`
Expected: clean, including the `implicit_reexport = false` check from Task 1 - `pty/__init__.py` now does real re-exports, all correctly listed in `__all__`.

- [ ] **Step 7: Commit**

```bash
git add src/devtemplate/pty
git commit -m "refactor(dvt): explicit __all__ across the pty package, share CHUNK/DRAIN_TIMEOUT

CHUNK and DRAIN_TIMEOUT were duplicated verbatim between this package
and ssh_server.py with comments cross-referencing each other - moved
here since devtemplate.sshd (next task) already depends on this
package, not the reverse. CHANNEL_EVENTS stays independently defined
in each consumer; the two tuples differ for a real reason, not
accidental drift."
```

---

## Task 4: `devtemplate.sshd` package (replaces `ssh_server.py`)

**Files:**
- Create: `src/devtemplate/sshd/__init__.py`
- Create: `src/devtemplate/sshd/server.py`
- Create: `src/devtemplate/sshd/session.py`
- Create: `src/devtemplate/sshd/stdio.py`
- Delete: `src/devtemplate/ssh_server.py`
- Modify: `src/devtemplate/ssh.py`
- Rename: `tests/test_ssh_server.py` → `tests/test_sshd.py`

**Interfaces:**
- Consumes: `devtemplate.pty.{spawn_pty_process, bridge_to_ssh_process, CHUNK, DRAIN_TIMEOUT}` (Task 3).
- Produces: `devtemplate.sshd.run_stdio_server(cli_binary: str, container_name: str) -> int` (package-level, unchanged signature), `devtemplate.sshd.session.handle_process` (renamed from `_handle_process`, unchanged signature), `devtemplate.sshd.server.NoAuthServer` (renamed from `_NoAuthServer`).

- [ ] **Step 1: Create `src/devtemplate/sshd/stdio.py`**

```python
"""Bridges this process's real stdin/stdout to the socketpair end asyncssh
isn't using - the transport-level half of the dvt ssh --stdio bridge, kept
separate from session.py's session-level handling."""

from __future__ import annotations

import os
import socket
import sys
import threading

from devtemplate.pty import CHUNK

__all__ = ["pump_stdio_to_socket"]


def pump_stdio_to_socket(sock: socket.socket) -> None:
    """Bridge this process's real stdin/stdout to the socketpair end asyncssh
    isn't using. Runs in dedicated threads since it's blocking I/O on real
    file descriptors, alongside the asyncio event loop driving the server on
    the other end of the pair. Blocks until the server side closes.

    Reads/writes the raw file descriptors via `os.read`/`os.write` rather than
    `sys.stdin.buffer`/`sys.stdout.buffer` - discovered by driving this from a
    real `ssh` client's `ProxyCommand`, where it deadlocked. The root cause is
    portable, not platform-specific: `io.BufferedReader.read(n)` keeps issuing
    raw reads until it has accumulated `n` bytes (or hit real EOF) rather than
    returning as soon as *any* data is available - correct for reading a file,
    wrong for pumping a live, sub-buffer-sized, latency-sensitive byte stream
    like an SSH handshake. On every OS this can block forever waiting for
    bytes the peer has already finished sending, because the peer is waiting
    on a reply to what it just sent. On this Windows dev machine, with Git for
    Windows' (Cygwin/MSYS) `ssh.exe` as the parent, the same wrong semantics
    surfaced as a *false* empty read instead of a hang - Cygwin's pipe
    implementation happened to turn "not enough buffered yet" into a
    zero-length read partway through, which `sys.stdin.buffer.read()` cannot
    tell apart from real EOF, so the bridge shut down the connection thinking
    the client was done. `sys.stdout.buffer.write()` independently raised
    `OSError: [Errno 22] Invalid argument` on the same Cygwin-piped stdout.
    Both are symptoms of the same underlying mismatch, not two unrelated
    Windows bugs - a POSIX `ssh` parent would very plausibly just hang instead
    of erroring, which is *worse* (no diagnostic, indistinguishable from a
    slow network) rather than better. Reading/writing the fds directly with
    `os.read`/`os.write` returns on first-available data like a byte pump
    needs, is correct on every platform, and has been verified end to end
    against a real `ssh` client through this exact path. Do not reintroduce
    the buffered wrapper, and do not guard this behind `sys.platform`."""

    def stdin_to_sock() -> None:
        stdin_fd = sys.stdin.fileno()
        try:
            while True:
                data = os.read(stdin_fd, CHUNK)
                if not data:
                    break
                sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # Server closed the socket first; nothing more to forward.

    def sock_to_stdout() -> None:
        stdout_fd = sys.stdout.fileno()
        try:
            while True:
                data = sock.recv(CHUNK)
                if not data:
                    break
                # os.write() may write fewer bytes than given (e.g. a signal
                # interrupting a partial transfer) - unlike BufferedWriter's
                # all-or-raise `.write()`, so a single call here could
                # silently truncate the SSH stream. Loop until it's all sent.
                while data:
                    data = data[os.write(stdout_fd, data) :]
        except OSError:
            pass

    reader = threading.Thread(target=stdin_to_sock, daemon=True)
    writer = threading.Thread(target=sock_to_stdout, daemon=True)
    reader.start()
    writer.start()
    writer.join()  # server-side close (EOF from the socket) ends the bridge
```

- [ ] **Step 2: Create `src/devtemplate/sshd/session.py`**

```python
"""Bridges one opened SSH session to a docker/podman exec subprocess - the
pty branch delegates to devtemplate.pty, the non-pty branch runs its own
plain-pipe pumps here. The pty-requesting-session counterpart to this
module is devtemplate.pty.bridge.bridge_to_ssh_process; see that module's
own docstring for the other half of this split."""

from __future__ import annotations

import asyncio
import codecs
import contextlib

import asyncssh

from devtemplate.pty import CHUNK, bridge_to_ssh_process, spawn_pty_process

__all__ = ["handle_process"]

CHANNEL_EVENTS = (
    asyncssh.misc.TerminalSizeChanged,
    asyncssh.misc.BreakReceived,
    asyncssh.misc.SignalReceived,
)
"""Out-of-band channel events asyncssh delivers by *raising* them out of a
stream read (see `stream.py`'s `exception_received`) instead of returning data.

Each means "nothing to forward this time, keep reading" - emphatically *not*
"this stream is finished". asyncssh accepts pty requests by default, and
OpenSSH sends a `window-change` on every terminal resize, so treating one of
these as end-of-input would kill the user's keystrokes for the rest of an
ordinary interactive session the first time they resized their window.

`SoftEOFReceived` is deliberately absent: asyncssh converts it into a normal
empty read internally and never raises it here. All of these derive from
`Exception` directly rather than `asyncssh.Error`, so they need listing
separately from genuine protocol failures. NOT shared with
devtemplate.pty.bridge's own CHANNEL_EVENTS - see this package's own
Global Constraints note (in the plan this code came from) for why the two
tuples differ."""


async def handle_process(
    process: asyncssh.SSHServerProcess, cli_binary: str, container_name: str
) -> int:
    """Bridge one opened SSH session to a `docker/podman exec -i` subprocess -
    the same exec mechanism `exec_interactive` already uses, just with pipes
    instead of inherited stdio (asyncssh owns the actual terminal now).

    Honours both kinds of session an SSH client can ask for. `process.command`
    is `None` for a bare shell request (`ssh host`) and carries the requested
    command line for an exec request (`ssh host "echo hi"`); the latter must
    actually run that command rather than dropping the client into an
    interactive shell that ignores it. Tools driving this as a `ProxyCommand`
    - JetBrains Gateway especially - rely almost entirely on exec requests.

    Reports the subprocess's exit status to the SSH client *and* returns it,
    so the caller can use it as its own process exit code. `process.exit()`
    only sets the SSH channel's status; it hands nothing back to Python.

    If the client requested a pty (`process.get_terminal_type()` is not
    `None`), bridges to a real host-side pseudo-terminal instead (see
    `devtemplate.pty`) - `-it` rather than `-i`, so the container's shell
    gets a real tty. Non-pty exec requests are completely unaffected by this
    branch.
    """
    # A bare shell request runs the container user's own configured shell
    # (falling back to sh if $SHELL isn't set) rather than hardcoding sh -
    # images that wire up shell-startup hooks (e.g. a pixi project's
    # `pixi shell-hook` in .bashrc/fish's conf.d) only fire under that real
    # shell. An exec request's command is passed as its own distinct argv
    # entry, reaching the container's `sh -c` exactly as the client wrote it -
    # no shell runs on this side.
    shell_argv = (
        ["sh", "-c", 'exec "${SHELL:-sh}"']
        if process.command is None
        else ["sh", "-c", process.command]
    )

    term_type = process.get_terminal_type()
    if term_type is not None:
        width, height, _, _ = process.get_terminal_size()
        # A client may legally request a pty without stating its dimensions,
        # in which case RFC 4254 says the zero values must be ignored -
        # asyncssh's own client does exactly this unless given term_size, and
        # get_terminal_size() then reports (0, 0, 0, 0). The POSIX backend
        # tolerates a 0x0 pty, but ConPTY rejects it outright ("PTY cols and
        # rows must be positive and non-zero"), and with this feature's
        # deliberate no-fallback policy that would kill the session with exit
        # 255 - reproducing the very "session looks broken" bug this branch
        # exists to fix, for a client that did nothing wrong. 80x24 is the
        # conventional default an unsized terminal gets.
        #
        # -e TERM=... is passed explicitly because docker/podman exec -it
        # otherwise defaults the container's TERM to xterm regardless of what
        # the client actually asked for - a real sshd forwards the client's
        # TERM, and without this a client on xterm-256color (or similar)
        # silently loses color capability in full-screen programs like
        # vim/htop inside the container.
        pty_proc = spawn_pty_process(
            [
                cli_binary,
                "exec",
                "-it",
                "-e",
                f"TERM={term_type}",
                container_name,
                *shell_argv,
            ],
            rows=height or 24,
            cols=width or 80,
        )
        return await bridge_to_ssh_process(pty_proc, process)

    proc = await asyncio.create_subprocess_exec(
        cli_binary,
        "exec",
        "-i",
        container_name,
        *shell_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Kept *separate* from stdout, and pumped onto SSH's own extended-data
        # stderr channel below. Merging the two (stderr=STDOUT) looks like
        # harmless `2>&1` fidelity until you remember what is actually being
        # spawned: the docker/podman CLI wrapper, which emits its own warnings
        # (podman's `WARN[0000]...` lines are routine) on stderr. Merged, those
        # land inside the *command's* stdout - silently corrupting it for any
        # tool that parses an exec'd command's output, which is exactly how
        # VS Code Remote-SSH and JetBrains Gateway drive a remote host.
        stderr=asyncio.subprocess.PIPE,
    )
    proc_stdin = proc.stdin
    proc_stdout = proc.stdout
    proc_stderr = proc.stderr
    assert proc_stdin is not None
    assert proc_stdout is not None
    assert proc_stderr is not None

    async def pump_client_to_process() -> None:
        # Only two things genuinely end this direction: the container's shell
        # exiting (OSError/BrokenPipeError) or the client vanishing mid-session
        # (asyncssh.ConnectionLost and friends, under asyncssh.Error). Neither
        # may escape - this runs as a task the session teardown awaits, and an
        # exception here would skip reporting the session's exit status.
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                try:
                    data = await process.stdin.read(CHUNK)
                except CHANNEL_EVENTS:
                    # Handled per-read, not around the loop: these are events,
                    # not end-of-input, and must not stop us forwarding.
                    continue
                if not data:
                    break
                proc_stdin.write(data.encode() if isinstance(data, str) else data)
                await proc_stdin.drain()
        with contextlib.suppress(OSError):
            proc_stdin.close()

    async def pump_process_to_client(
        source: asyncio.StreamReader, sink: asyncssh.SSHWriter[str]
    ) -> None:
        # Same reasoning in the other direction: once the channel is gone there
        # is nowhere to put the container's output, which ends this pump but
        # still leaves a real exit code for `proc.wait()` to report. No
        # CHANNEL_EVENTS here - those come from reading the *client* stream,
        # and this pump reads the subprocess.
        #
        # The decoder is incremental, and deliberately created *per call* so
        # the stdout and stderr pumps never share one: `chunk.decode()` on each
        # raw read destroys any multi-byte UTF-8 character straddling a read
        # boundary (a 3-byte character split 1/2 across two reads becomes two
        # replacement characters), which output longer than one `CHUNK` hits
        # routinely - accented text, box drawing, non-ASCII filenames. An
        # incremental decoder holds the partial sequence over to the next read
        # and reassembles it. Feeding one decoder from two interleaved streams
        # would splice their partial sequences together instead.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                chunk = await source.read(CHUNK)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    sink.write(text)
            # Flush whatever a truncated final sequence left pending, so a
            # stream ending mid-character still terminates deterministically.
            tail = decoder.decode(b"", final=True)
            if tail:
                sink.write(tail)

    # The client half is a background task rather than a `gather` partner: a
    # client is under no obligation to ever send EOF, so waiting on it would
    # hang every session whose shell exits on its own. The two output pumps
    # *are* gathered: both must drain fully before the exit status is reported,
    # or the tail of either stream races the channel closing.
    client_pump = asyncio.create_task(pump_client_to_process())
    try:
        await asyncio.gather(
            pump_process_to_client(proc_stdout, process.stdout),
            pump_process_to_client(proc_stderr, process.stderr),
        )
        exit_code = await proc.wait()
    finally:
        # Belt and braces: the pump suppresses its own I/O failures, but this
        # runs in a `finally`, so anything escaping here would replace the real
        # outcome (including a failure being reported) with itself.
        client_pump.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await client_pump

    process.exit(exit_code)
    return exit_code
```

- [ ] **Step 3: Create `src/devtemplate/sshd/server.py`**

```python
"""A real (if minimal) SSH server for `dvt ssh --stdio <name>`.

`ProxyCommand` only replaces the *transport* an SSH client talks over - the
client still performs a full SSH protocol exchange (version banner, key
exchange, authentication, channel open) across it. Piping straight to a bare
`docker exec -i <name> sh` therefore cannot work: a shell cannot speak SSH.

This module runs an actual `asyncssh` server bound to this process's own
stdin/stdout (via an internal `socket.socketpair()` bridged by blocking-I/O
threads), and forwards each session it accepts to `docker`/`podman exec`
against `<container>` - `-it` with a real host-side pty for sessions that
requested one (see `devtemplate.pty`), `-i` otherwise. That is what makes
`dvt ssh --stdio` usable as a `ProxyCommand` target - by JetBrains Gateway,
VS Code Remote-SSH, or plain `ssh` - without baking an `sshd` into every
workspace image.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import threading

import asyncssh

from devtemplate.pty import DRAIN_TIMEOUT
from devtemplate.sshd.session import handle_process
from devtemplate.sshd.stdio import pump_stdio_to_socket

__all__ = ["run_stdio_server", "NoAuthServer"]

EXIT_SESSION_FAILED = 255
"""Exit code for a session that never got off the ground - `docker`/`podman`
missing from PATH, container gone, transport died mid-handshake. 255 is ssh's
own convention for "the connection itself failed", as distinct from any exit
code the remote command could have produced."""


class NoAuthServer(asyncssh.SSHServer):
    """No real authentication - see this feature's design spec for why: the
    socketpair this server listens on is only ever reachable by a local
    subprocess spawn, which already requires local shell access (the actual
    security boundary). Requiring SSH-level auth on top would check a
    credential that adds no real security."""

    def begin_auth(self, username: str) -> bool:
        """Returning `False` tells asyncssh no authentication is required."""
        return False


def run_stdio_server(cli_binary: str, container_name: str) -> int:
    """Run a real (if minimal) SSH server bound to this process's own
    stdin/stdout, bridging the one session it'll ever handle to
    `docker`/`podman exec` against `container_name` - `-it` with a real
    host-side pty if the session requested one, `-i` otherwise (see
    `devtemplate.sshd.session.handle_process`). Returns the bridged process's
    exit code.

    This is deliberately the one fallible entry point here that returns a
    plain `int` rather than a `Result`: it exists to turn a session into a
    process exit code for `dvt ssh --stdio`.
    """
    # `process.exit()` sets the SSH channel's status for the client, not a
    # Python return value, so `handle_process`'s own return travels back out
    # through this list. Nothing yields between `handle_process` returning
    # and the append, so the value is always recorded before the connection
    # can finish closing.
    exit_codes: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        try:
            exit_codes.append(
                await handle_process(process, cli_binary, container_name)
            )
        except Exception as exc:
            # asyncssh logs a failing process_factory at debug level and force-
            # closes the connection, so without this a session that never even
            # spawned (`docker` not on PATH, container gone) would be
            # indistinguishable from a clean one: exit code 0, no diagnostic.
            # stderr is safe to write - only stdout carries the SSH stream.
            # Name the binary and container: the most likely failure is a
            # missing CLI, and OSError's own message on Windows is just "The
            # system cannot find the file specified" with no hint which file.
            print(
                f"dvt ssh --stdio: session for container {container_name!r} "
                f"via {cli_binary!r} failed: {exc}",
                file=sys.stderr,
                flush=True,
            )
            exit_codes.append(EXIT_SESSION_FAILED)
            # Best-effort: tell the client too, if the channel is still alive.
            with contextlib.suppress(Exception):
                process.exit(EXIT_SESSION_FAILED)

    async def main(server_sock: socket.socket) -> None:
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        conn = await asyncssh.run_server(
            server_sock,
            server_factory=NoAuthServer,
            server_host_keys=[host_key],
            process_factory=process_factory,
            # We accept every client unconditionally (see `NoAuthServer`), so
            # GSSAPI is dead weight - and leaving it enabled makes asyncssh
            # resolve this host's FQDN at startup, which costs seconds of
            # connection latency on machines behind slow reverse DNS.
            gss_host=None,
            # asyncssh defaults this to True, which activates its own
            # line-editing convenience layer for any session that requests a
            # pty (term_type set + encoding not None - exactly the condition
            # handle_process's pty branch handles) and silently mangles raw
            # input in the process: a client sending "hello\r\n" arrives at
            # the pty as "hello\n\n". That defeats the entire point of a real
            # pty bridge - the container's own shell is supposed to do the
            # real line editing/echo, exactly as a real `sshd` hands a real
            # terminal's raw bytes straight through untouched. Non-pty exec
            # sessions never activate this option either way, so this has no
            # effect on the plain-pipe path below.
            line_editor=False,
        )
        await conn.wait_closed()

    server_sock, stdio_sock = socket.socketpair()
    # A plain daemon thread, not `asyncio.to_thread`: the latter runs on the
    # loop's shared executor, which `asyncio.run` joins on shutdown with a
    # 300s timeout of its own - so cancelling the future would not stop the
    # thread, and a bridge still blocked in `recv` would hang the process for
    # five minutes rather than the five seconds documented above.
    bridge = threading.Thread(
        target=pump_stdio_to_socket, args=(stdio_sock,), daemon=True
    )
    bridge.start()
    try:
        asyncio.run(main(server_sock))
    finally:
        # Closing the server end is what gives the bridge its EOF, and asyncio
        # has normally already done it by now; joining outside the loop lets
        # the bridge flush its last output without blocking anything.
        bridge.join(DRAIN_TIMEOUT)
        for sock in (stdio_sock, server_sock):
            with contextlib.suppress(OSError):
                sock.close()

    # No session ever opened (client connected and hung up) - not an error.
    return exit_codes[-1] if exit_codes else 0
```

- [ ] **Step 4: Create `src/devtemplate/sshd/__init__.py`**

```python
from devtemplate.sshd.server import run_stdio_server

__all__ = ["run_stdio_server"]
```

- [ ] **Step 5: Delete the old flat module**

```bash
git rm src/devtemplate/ssh_server.py
```

- [ ] **Step 6: Update `src/devtemplate/ssh.py`**

`stdio_proxy` currently does a deferred import (inside the function body, not at module scope) specifically to avoid `asyncssh`'s import cost on every CLI invocation:

```python
    from devtemplate import ssh_server

    container = find_workspace_container(client, name)
    if container is None or container.name is None:
        raise ValueError(f"No workspace named {name!r} is running.")
    return ssh_server.run_stdio_server(cli_binary, container.name)
```

Change to (still a deferred, function-local import - same startup-cost reasoning, just importing the renamed package):

```python
    from devtemplate.sshd import run_stdio_server

    container = find_workspace_container(client, name)
    if container is None or container.name is None:
        raise ValueError(f"No workspace named {name!r} is running.")
    return run_stdio_server(cli_binary, container.name)
```

Also update the docstring comment above it (currently references `ssh_server pulls in asyncssh` - change `ssh_server` to `devtemplate.sshd`), and the `stdio_proxy` docstring's own reference to `ssh_server.run_stdio_server` (change to `devtemplate.sshd.run_stdio_server`).

- [ ] **Step 7: Rename and update the test file**

```bash
git mv tests/test_ssh_server.py tests/test_sshd.py
```

In `tests/test_sshd.py`, change line 15 from:

```python
from devtemplate.ssh_server import _handle_process, _NoAuthServer, run_stdio_server
```

to:

```python
from devtemplate.sshd import run_stdio_server
from devtemplate.sshd.server import NoAuthServer
from devtemplate.sshd.session import handle_process
```

Then update every use of `_handle_process` to `handle_process` and every use of `_NoAuthServer` to `NoAuthServer` throughout the rest of the file (grep for both to find every call site - this file has multiple tests exercising both names).

- [ ] **Step 8: Run the test suite to verify RED, then fix, then GREEN**

Before Step 7's edits are applied, run: `pixi run pytest tests/test_sshd.py -v --no-cov`
Expected: FAIL with `ImportError: cannot import name '_handle_process' from 'devtemplate.sshd'` (or similar - the package's `__init__.py` only exports `run_stdio_server`). If you already applied Step 7 before running this, that's fine too - the point is to have genuinely seen this fail at some point during the task, not to force a specific ordering.

After Step 7's edits: run `pixi run pytest tests/test_sshd.py -v --no-cov`
Expected: all tests pass, same count as the file had before this task.

- [ ] **Step 9: Grep for stale references**

Run: `grep -rn "ssh_server\|_handle_process\|_NoAuthServer\|_pump_stdio_to_socket" src/ tests/`
Expected: zero results. Every reference to the old module name and the old underscore names must be gone.

- [ ] **Step 10: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this task, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 11: Commit**

```bash
git add src/devtemplate/sshd src/devtemplate/ssh_server.py src/devtemplate/ssh.py tests/test_sshd.py
git commit -m "refactor(dvt): split ssh_server.py into an sshd/ package

_handle_process was the structural twin of pty/bridge.py's already-
public bridge_to_ssh_process, just never given its own module. Splits
into server.py (run_stdio_server + NoAuthServer), session.py
(handle_process, both branches), and stdio.py (the raw stdin/stdout
transport bridge) - each now genuinely public within the package,
consuming pty's newly-shared CHUNK/DRAIN_TIMEOUT instead of
duplicating them."
```

---

## Task 5: `devtemplate.workspace` package (replaces `workspace.py` + `workspace_lookup.py`)

**Files:**
- Create: `src/devtemplate/workspace/__init__.py`
- Create: `src/devtemplate/workspace/up.py`
- Create: `src/devtemplate/workspace/existing.py`
- Create: `src/devtemplate/workspace/lookup.py`
- Delete: `src/devtemplate/workspace.py`
- Delete: `src/devtemplate/workspace_lookup.py`
- Modify: `src/devtemplate/cli.py`
- Modify: `tests/test_workspace.py`
- Modify: `tests/test_workspace_lookup.py`

**Interfaces:**
- Consumes: nothing new from other tasks (its `from devtemplate.features import pull_feature` line is unaffected by Task 2 - that import path was already package-level and stays identical whether or not Task 2 has run).
- Produces: `devtemplate.workspace.up_workspace` (unchanged signature, package-level), `devtemplate.workspace.resolve_for_up`, `devtemplate.workspace.resolve_existing` (both unchanged signatures, now package-level instead of `devtemplate.workspace_lookup`).

- [ ] **Step 1: Create `src/devtemplate/workspace/lookup.py`**

This is the current `workspace_lookup.py`, content unchanged except renaming its two private helpers and adding `__all__`:

```python
from __future__ import annotations

from pathlib import Path

from docker.client import DockerClient
from logerr.utilities import wrap_result

from devtemplate.container import (
    find_workspace_container,
    find_workspace_containers_by_folder,
)

__all__ = ["resolve_for_up", "resolve_existing"]


def names_by_folder(client: DockerClient, cwd: Path) -> list[str]:
    containers = find_workspace_containers_by_folder(client, cwd)
    return sorted(
        name
        for name in (container.labels.get("dvt.workspace") for container in containers)
        if name
    )


def multiple_matches_error(command: str, names: list[str]) -> Exception:
    return ValueError(
        f"Multiple workspaces match this folder: {', '.join(names)}. "
        f"Run 'dvt {command} <name>' with one of these."
    )


@wrap_result
def resolve_for_up(client: DockerClient, name: str | None, cwd: Path) -> str:
    """Turn dvt up's optional name into a concrete one. An explicit name passes
    through unchanged. When omitted: exactly one workspace already tied to this
    folder (via its devcontainer.local_folder label) reuses that name; none yet
    falls back to the folder's own directory name, to create a fresh workspace
    (matching dvt init's own default-name derivation) - unless a workspace
    already exists under that name for a *different* folder, in which case
    this refuses rather than silently resuming someone else's workspace; more
    than one folder match refuses too, listing every candidate, since dvt
    won't guess which one you meant.
    """
    if name is not None:
        return name
    names = names_by_folder(client, cwd)
    if len(names) == 1:
        return names[0]
    if names:
        raise multiple_matches_error("up", names)

    fallback_name = cwd.resolve().name
    existing = find_workspace_container(client, fallback_name)
    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        if existing_folder != str(cwd.resolve()):
            raise ValueError(
                f"A workspace named '{fallback_name}' already exists for a "
                f"different folder ({existing_folder or 'unknown'}). "
                "Pass an explicit name for this one."
            )
    return fallback_name


@wrap_result
def resolve_existing(
    client: DockerClient, name: str | None, cwd: Path, command: str
) -> str:
    """Same shape as resolve_for_up, for commands that only ever act on a
    workspace that already exists (ssh/stop/delete) - so no matches is also a
    refusal, not a directory-name fallback. `command` names the actual command
    that was run, so the refusal's suggested next step is accurate.
    """
    if name is not None:
        return name
    names = names_by_folder(client, cwd)
    if len(names) == 1:
        return names[0]
    if not names:
        raise ValueError(
            "No workspace found for this folder. Specify a name, "
            "or run 'dvt up' to create one."
        )
    raise multiple_matches_error(command, names)
```

- [ ] **Step 2: Create `src/devtemplate/workspace/existing.py`**

The existing-container reconciliation cluster, extracted from `workspace.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Result
from logerr.utilities import wrap_result

from devtemplate.container import read_stored_config
from devtemplate.ssh import write_ssh_config_entry

__all__ = [
    "resume_existing",
    "config_drift_error",
    "folder_mismatch_error",
    "rebuild_teardown",
]


def refresh_ssh_config(name: str) -> Result[None, Exception]:
    return write_ssh_config_entry(name, Path.home() / ".ssh" / "config")


@wrap_result
def resume_existing(existing: Container, name: str) -> Container:
    """Handle the re-`up` case where a container already carries this
    workspace's label: start it if it isn't running, then (re)write its SSH
    config entry. Every fallible operation on `existing` - status access and
    start(), both of which can raise docker-py's APIError - is wrapped, so
    nothing escapes as a bare exception.
    """
    if existing.status != "running":
        existing.start()
    refresh_ssh_config(name).unwrap()
    return existing


def config_drift_error(
    existing: Container, config: dict[str, Any], name: str
) -> Exception:
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
        "'dvt up --rebuild' to rebuild it, or revert devcontainer.json and "
        f"run 'dvt up' again. To use the existing container without going "
        f"through 'up' at all, run 'dvt ssh {name}'."
    )


def folder_mismatch_error(
    existing_folder: str | None, project_path: Path, name: str
) -> Exception:
    """Build the Err raised when --rebuild is invoked for a workspace that
    isn't *confirmed* to belong to the folder dvt is currently running from -
    either its devcontainer.local_folder label names a different folder, or
    the label is missing entirely (so there's nothing to confirm against at
    all). Rebuilding from the wrong vantage point, or an unconfirmed one,
    would tear down the real workspace and rebuild it with an unrelated
    project's config, so this refuses outright and leaves the container
    completely untouched."""
    if existing_folder is None:
        return ValueError(
            f"Workspace {name!r} exists but dvt can't confirm it was built "
            f"from '{project_path.resolve()}' (it has no "
            "devcontainer.local_folder label to check). Refusing to "
            "--rebuild it from here - run 'dvt up --rebuild' from the "
            "workspace's own folder instead."
        )
    return ValueError(
        f"Workspace {name!r} was built from {existing_folder!r}, but dvt is "
        f"running from '{project_path.resolve()}'. Run 'dvt up --rebuild' "
        "from the workspace's own folder instead."
    )


@wrap_result
def rebuild_teardown(
    client: DockerClient, existing: Container, image_tag: str
) -> None:
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
```

Note: `refresh_ssh_config` is used by both `resume_existing` (in this file) and, after Step 3, by `up_workspace` in `up.py` at the very end of its pipeline - `up.py` will need its own copy or its own import of this. Since it's a one-line wrapper with no state, define it once here and import it into `up.py` (see Step 3) rather than duplicating it - it's not listed in this file's `__all__` since it's not part of `existing.py`'s own public contract, but Python doesn't stop `up.py` importing it directly from this sibling module, matching this whole plan's "enforcement is real but narrow" stance.

- [ ] **Step 3: Create `src/devtemplate/workspace/up.py`**

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
from docker.models.containers import Container
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate import podman_machine
from devtemplate.build import build_image
from devtemplate.config import Settings
from devtemplate.container import (
    config_has_drifted,
    find_workspace_container,
    refuse_unsupported,
    run_container,
    run_lifecycle_commands,
)
from devtemplate.features import pull_feature
from devtemplate.runtime import RuntimeHandle
from devtemplate.workspace.existing import (
    config_drift_error,
    folder_mismatch_error,
    rebuild_teardown,
    refresh_ssh_config,
    resume_existing,
)

__all__ = ["up_workspace"]


def feature_id(ref: str) -> str:
    """Derive a short id from an OCI ref's trailing path segment, e.g.
    'ghcr.io/jesserobertson/devcontainers/fastapi:latest' -> 'fastapi'. Used only
    for Dockerfile stage naming, not read from the Feature's own
    devcontainer-feature.json "id" field - an acceptable v1 simplification since
    this repo's own Features always keep the two in sync by construction."""
    return ref.rsplit("/", 1)[-1].split(":")[0]


@wrap_result
def load_config(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"{config_file} not found. Run 'dvt init' first.")
    return cast(dict[str, Any], json.loads(config_file.read_text()))


def image_tag(name: str) -> str:
    """The image tag dvt builds and tags a workspace's image under. Factored
    out so the tag literal used to remove the cached image (rebuild_teardown)
    and the one used to build the fresh image (build_image) can't drift apart."""
    return f"dvt/{name}:latest"


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
    `rebuild` is True, the config is loaded and validated *before* anything
    is torn down - only once that succeeds does dvt remove the existing
    container and its cached image tag (regardless of whether config
    actually drifted - `--rebuild` is also the general force-fresh escape
    hatch for e.g. a moved upstream base image tag), then falls through into
    the same build-from-scratch sequence used when no container exists yet,
    with Docker's build cache and base-image reuse both disabled.

    The existing container's `devcontainer.local_folder` label is checked
    against `project_path` before any of this, but the two branches need
    different levels of confidence in that check:

    - Without `--rebuild`, a *confirmed* mismatch (label present and
      different) skips the drift check entirely and just resumes - dvt can't
      meaningfully evaluate drift from the wrong vantage point. A *missing*
      label (foreign/pre-feature container, nothing to check against) falls
      back to the normal drift-check behavior below - resuming is
      non-destructive either way, so the lenient reading costs nothing.
    - With `--rebuild`, anything short of an *affirmatively confirmed* match
      (label present and equal) refuses outright, treating "missing label"
      the same as "confirmed mismatch": proceeding would tear down the
      existing container on nothing more than the assumption it belongs to
      `project_path`, which is exactly the hazard this check exists to close.
    """
    existing = find_workspace_container(handle.client, name)
    config_file = project_path / ".devcontainer" / "devcontainer.json"

    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        resolved_project_path = str(project_path.resolve())

        if not rebuild:
            # Lenient: a missing label isn't treated as a mismatch, since
            # resuming an unconfirmed container is non-destructive.
            folder_confirmed_mismatch = (
                existing_folder is not None and existing_folder != resolved_project_path
            )
            if not folder_confirmed_mismatch:
                config_result = load_config(config_file)
                if config_result.is_ok():
                    current_config = config_result.unwrap()
                    if config_has_drifted(existing, current_config):
                        raise config_drift_error(existing, current_config, name)
            return resume_existing(existing, name).unwrap()

        # Strict: --rebuild tears the container down, so it requires an
        # affirmatively confirmed match, not merely "not confirmed to differ".
        folder_confirmed_match = existing_folder == resolved_project_path
        if not folder_confirmed_match:
            raise folder_mismatch_error(existing_folder, project_path, name)

    config = load_config(config_file).unwrap()

    refuse_unsupported(config).unwrap()

    if "image" not in config:
        raise ValueError(
            f'{config_file} has no top-level "image" - only image-based '
            "devcontainer.json is supported"
        )

    if existing is not None:
        rebuild_teardown(handle.client, existing, image_tag(name)).unwrap()

    features_config = config.get("features", {})
    feature_refs = list(features_config.keys())

    with httpx.Client() as http_client:
        pulled = traverse_result(
            feature_refs,
            lambda ref: pull_feature(http_client, ref, settings.data_dir / "features"),
        ).unwrap()

    features = [
        (feature_id(ref), extracted_dir, features_config[ref])
        for ref, extracted_dir in zip(feature_refs, pulled, strict=True)
    ]

    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        podman_machine.ensure_gpu_support(
            handle.cli_binary, handle.machine_name
        ).unwrap()

    with tempfile.TemporaryDirectory() as scratch:
        image_tag_value = build_image(
            handle.client,
            config["image"],
            features,
            image_tag(name),
            Path(scratch),
            nocache=rebuild,
            pull=rebuild,
        ).unwrap()

    container = run_container(
        handle.client, image_tag_value, config, name, project_path, config_file
    ).unwrap()

    run_lifecycle_commands(container, config).unwrap()

    refresh_ssh_config(name).unwrap()

    return container
```

Note the one necessary non-cosmetic change from the original: the original code (confirmed by reading the live file during plan research) binds `build_image`'s return value to a local variable literally named `image_tag` - harmless today because the module-level function is `_image_tag` (underscore, a different name). Once that function is renamed to `image_tag` in this task, the local variable would shadow it within the same function body. The code above avoids this by naming the local `image_tag_value` instead - a required rename, not optional cleanup.

- [ ] **Step 4: Create `src/devtemplate/workspace/__init__.py`**

```python
from devtemplate.workspace.lookup import resolve_existing, resolve_for_up
from devtemplate.workspace.up import up_workspace

__all__ = ["up_workspace", "resolve_for_up", "resolve_existing"]
```

- [ ] **Step 5: Delete the old flat modules**

```bash
git rm src/devtemplate/workspace.py src/devtemplate/workspace_lookup.py
```

- [ ] **Step 6: Update `src/devtemplate/cli.py`**

Change:

```python
from devtemplate.workspace import up_workspace
from devtemplate.workspace_lookup import resolve_existing, resolve_for_up
```

to:

```python
from devtemplate.workspace import resolve_existing, resolve_for_up, up_workspace
```

(Two import lines collapse into one - both names now come from the same package.)

- [ ] **Step 7: Run the test suite to verify RED, then update imports, then GREEN**

Run: `pixi run pytest tests/test_workspace.py tests/test_workspace_lookup.py -v`
Expected (before updating the test files): FAIL with `ModuleNotFoundError: No module named 'devtemplate.workspace_lookup'` (for `test_workspace_lookup.py`) - `test_workspace.py`'s own import (`from devtemplate.workspace import up_workspace`) will actually still resolve fine, since the package-level import path is unchanged; only `test_workspace_lookup.py` is guaranteed to fail here.

In `tests/test_workspace_lookup.py`, change:

```python
from devtemplate.workspace_lookup import resolve_existing, resolve_for_up
```

to:

```python
from devtemplate.workspace import resolve_existing, resolve_for_up
```

`tests/test_workspace.py` needs no import changes (its `from devtemplate.workspace import up_workspace` and `from devtemplate.container import compute_labels` both already resolve correctly against the new package).

Run: `pixi run pytest tests/test_workspace.py tests/test_workspace_lookup.py -v`
Expected: all tests pass, same counts as before this task.

- [ ] **Step 8: Grep for stale references**

Run: `grep -rn "workspace_lookup\|_feature_id\|_load_config\|_refresh_ssh_config\|_resume_existing\|_config_drift_error\|_folder_mismatch_error\|_image_tag\|_rebuild_teardown\|_names_by_folder\|_multiple_matches_error" src/ tests/`
Expected: zero results.

- [ ] **Step 9: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this task, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add src/devtemplate/workspace src/devtemplate/workspace.py src/devtemplate/workspace_lookup.py src/devtemplate/cli.py tests/test_workspace.py tests/test_workspace_lookup.py
git commit -m "refactor(dvt): merge workspace.py and workspace_lookup.py into a workspace/ package

Three files sharing a filename prefix undersold that they're one
cohesive concern. up.py keeps the linear build pipeline; existing.py
holds the existing-container reconciliation policy (now independently
testable as its own unit); lookup.py is workspace_lookup.py's content,
unchanged, just relocated."
```

---

## Task 6: `devtemplate.commands` package gains a populated `__init__.py`

**Files:**
- Modify: `src/devtemplate/commands/__init__.py`
- Modify: `src/devtemplate/commands/feature.py`
- Modify: `src/devtemplate/commands/info.py`
- Modify: `src/devtemplate/commands/init.py`
- Modify: `src/devtemplate/cli.py`

**Interfaces:**
- Produces: `devtemplate.commands.feature_app` (the Typer sub-app, was accessed as `feature.app`), `devtemplate.commands.info`, `devtemplate.commands.init` (both unchanged Typer command functions, now importable from the package directly).
- Consumes: nothing new from other tasks.

Note: `src/devtemplate/schemas/__init__.py` needs no change - confirmed during plan research that `schemas/` holds no Python code at all, only a vendored JSON schema file accessed via `importlib.resources` from `schema.py` (a different, flat module). An empty `__init__.py` is already correct there.

- [ ] **Step 1: Add `__all__` to `commands/feature.py`**

Add near the top, after the imports and before `app = typer.Typer(...)`:

```python
__all__ = ["app"]
```

Rename its three underscore-prefixed helpers, dropping the leading underscore: `_feature_ref` → `feature_ref`, `_add_one` → `add_one`, `_remove_one` → `remove_one`. Update the four call sites that reference them (`feature_ref` is called once inside `list_features`; `add_one` is called once inside `add`; `remove_one` is called once inside `remove`).

- [ ] **Step 2: Add `__all__` to `commands/info.py`**

Add near the top, after the imports:

```python
__all__ = ["info"]
```

No renames needed - `info` has no underscore prefix already.

- [ ] **Step 3: Add `__all__` to `commands/init.py`**

Add near the top, after the imports:

```python
__all__ = ["init", "DEFAULT_IMAGE"]
```

Rename `_scaffold_pixi_toml` → `scaffold_pixi_toml`, and update its one call site inside `init`.

- [ ] **Step 4: Populate `commands/__init__.py`**

Currently empty (0 bytes). Replace with:

```python
from devtemplate.commands.feature import app as feature_app
from devtemplate.commands.info import info
from devtemplate.commands.init import init

__all__ = ["feature_app", "info", "init"]
```

- [ ] **Step 5: Update `src/devtemplate/cli.py`**

Change:

```python
from devtemplate.commands import feature
from devtemplate.commands.info import info as info_command
from devtemplate.commands.init import init as init_command
```

to:

```python
from devtemplate.commands import feature_app, info as info_command, init as init_command
```

Then change the one use of `feature.app` (in `app.add_typer(feature.app, name="feature")`) to `feature_app` (`app.add_typer(feature_app, name="feature")`).

- [ ] **Step 6: Run the affected tests**

Run: `pixi run pytest tests/test_cli.py tests/test_cli_help.py tests/test_cli_version.py tests/test_feature_command.py tests/test_info_command.py tests/test_init.py -v`
Expected: all pass. `tests/test_init.py` still does `from devtemplate.commands.init import DEFAULT_IMAGE, init` (submodule-level, white-box) - unaffected by the package-level re-export added in Step 4, no change needed to that test file.

- [ ] **Step 7: Grep for stale references**

Run: `grep -rn "_feature_ref\|_add_one\|_remove_one\|_scaffold_pixi_toml\|from devtemplate.commands import feature\b" src/ tests/`
Expected: zero results (the last pattern specifically catches the old `import feature` module-object style that Step 5 replaced).

- [ ] **Step 8: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this task, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/devtemplate/commands src/devtemplate/cli.py
git commit -m "refactor(dvt): populate commands/__init__.py, explicit __all__ throughout

commands/ already had the right shape (empty __init__.py, dotted-path
imports) but never declared its public surface explicitly. cli.py now
imports feature_app/info/init from the package directly instead of
reaching into each submodule."
```

---

## Task 7: `__all__` for flat modules, batch A (no cross-file call-site changes)

**Files:**
- Modify: `src/devtemplate/container.py`
- Modify: `src/devtemplate/podman_machine.py`
- Modify: `src/devtemplate/merge.py`
- Modify: `src/devtemplate/runtime.py`

**Interfaces:**
- Produces: no new interfaces - every public name in these files keeps its exact current signature, only `__all__` is added and private helpers are renamed to drop their underscore (still not exported).
- Consumes: nothing.

These four files' underscore-prefixed helpers are confirmed (via the plan's research grep across the whole `src/` and `tests/` tree) to have no external call sites - every rename here is fully contained within its own file, and no test imports any of these underscore names directly. This is why they're batched together: same shape, independent, no ordering constraints between them or with any other task.

- [ ] **Step 1: `container.py`**

Add near the top, after the imports and the two existing module constants:

```python
__all__ = [
    "refuse_unsupported",
    "resolve_workspace",
    "compute_labels",
    "read_stored_config",
    "config_has_drifted",
    "run_container",
    "run_lifecycle_commands",
    "find_workspace_container",
    "find_workspace_containers_by_folder",
]
```

Rename, updating each function's own internal references and any call site elsewhere in this same file: `_encode_metadata` → `encode_metadata`, `_substitute_mount_variables` → `substitute_mount_variables`, `_parse_mount` → `parse_mount`, `_translate_run_args` → `translate_run_args`.

- [ ] **Step 2: `podman_machine.py`**

Add near the top, after the imports:

```python
__all__ = [
    "list_machines",
    "inspect_machine",
    "start_machine",
    "init_machine",
    "wait_until_ready",
    "check_gpu_cdi_ready",
    "install_nvidia_toolkit",
    "ensure_gpu_support",
    "ensure_machine_ready",
]
```

Rename: `_announce` → `announce`, `_run_podman_json` → `run_podman_json`, `_connection_url` → `connection_url`, `_inspect_and_connect` → `inspect_and_connect`, `_start_and_connect` → `start_and_connect`. Update internal call sites within this file.

- [ ] **Step 3: `merge.py`**

Add near the top, after the imports:

```python
__all__ = ["merge_layer", "merge_layers", "merge_layer_keys"]
```

Rename: `_merge_lifecycle_command` → `merge_lifecycle_command`, `_merge_feature_map` → `merge_feature_map`, `_merge_array_dedup` → `merge_array_dedup`, `_merge_array_concat` → `merge_array_concat`, `_merge_map` → `merge_map`. Update the dispatch table inside `merge_layer` that references these by name (a dict or if/elif chain - check the current file for its exact shape and update whichever it is).

- [ ] **Step 4: `runtime.py`**

Add near the top, after the imports:

```python
__all__ = ["RuntimeHandle", "get_client"]
```

Rename: `_try_docker` → `try_docker`, `_default_podman_socket` → `default_podman_socket`, `_resolve_podman` → `resolve_podman`, `_try_podman` → `try_podman`. Update internal call sites within this file (in particular, `get_client` calls into these).

- [ ] **Step 5: Run the affected tests**

Run: `pixi run pytest tests/test_container.py tests/test_podman_machine.py tests/test_merge.py tests/test_merge_properties.py tests/test_runtime.py -v`
Expected: all pass, no import changes needed in any of these test files (confirmed via the plan's research grep - none of them import an underscore name from these four modules).

- [ ] **Step 6: Grep for stale references**

Run: `grep -rn "_encode_metadata\|_substitute_mount_variables\|_parse_mount\b\|_translate_run_args\|_announce\b\|_run_podman_json\|_connection_url\|_inspect_and_connect\|_start_and_connect\|_merge_lifecycle_command\|_merge_feature_map\|_merge_array_dedup\|_merge_array_concat\|_merge_map\b\|_try_docker\|_default_podman_socket\|_resolve_podman\|_try_podman" src/ tests/`
Expected: zero results.

- [ ] **Step 7: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this task, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add src/devtemplate/container.py src/devtemplate/podman_machine.py src/devtemplate/merge.py src/devtemplate/runtime.py
git commit -m "refactor(dvt): explicit __all__ in container/podman_machine/merge/runtime

Mechanical: add __all__ listing each file's existing public surface,
drop the underscore prefix from internal-only helpers (all confirmed
to have zero external call sites). No behavior change."
```

---

## Task 8: `__all__` for flat modules, batch B (includes `cli.py` and `ssh.py` - run last)

**Files:**
- Modify: `src/devtemplate/cli.py`
- Modify: `src/devtemplate/ssh.py`
- Modify: `src/devtemplate/build.py`
- Modify: `src/devtemplate/store.py`
- Modify: `src/devtemplate/models.py`
- Modify: `src/devtemplate/config.py`
- Modify: `src/devtemplate/sidecar.py`
- Modify: `src/devtemplate/schema.py`
- Modify: `src/devtemplate/github.py`
- Modify: `src/devtemplate/cli_support.py`

**Interfaces:**
- Produces: no new interfaces - existing public signatures unchanged.
- Consumes: `devtemplate.sshd` (Task 4 - `ssh.py`'s deferred import), `devtemplate.workspace` and `devtemplate.commands` (Tasks 5 and 6 - `cli.py`'s imports). This task must run after Tasks 4, 5, and 6.

- [ ] **Step 1: `cli.py`**

Add near the top, after the imports and before `app = typer.Typer(...)`:

```python
__all__ = ["app", "main"]
```

Rename: `_version_callback` → `version_callback`, `_root_callback` → `root_callback`, `_find_or_exit` → `find_or_exit`. Update the two `@app.callback()`/`typer.Option(callback=...)` decorator references and the two call sites inside `stop`/`delete` that use `find_or_exit`.

- [ ] **Step 2: `ssh.py`**

Add near the top, after the imports and the two `_BEGIN_MARKER`/`_END_MARKER` constants:

```python
__all__ = ["write_ssh_config_entry", "remove_ssh_config_entry", "stdio_proxy", "exec_interactive"]
```

No renames needed - none of `ssh.py`'s four functions have an underscore prefix. `_BEGIN_MARKER`/`_END_MARKER` stay private (internal formatting constants, not part of this module's public contract) - leave their names as-is; per this plan's Naming convention, ALL module-level names lose their underscore prefix eventually, so rename these two as well: `_BEGIN_MARKER` → `BEGIN_MARKER`, `_END_MARKER` → `END_MARKER` (not exported via `__all__`, just no longer underscore-named). Update their two use sites inside `write_ssh_config_entry` and `remove_ssh_config_entry`.

- [ ] **Step 3: `build.py`**

Add near the top, after the imports:

```python
__all__ = ["generate_dockerfile", "build_image"]
```

Rename `_dockerfile_stage_name` → `dockerfile_stage_name`. Update its call site(s) within this file.

- [ ] **Step 4: `store.py`**

Add near the top, after the imports:

```python
__all__ = [
    "read_manifest",
    "write_manifest",
    "sync_templates",
    "list_cached_templates",
    "load_cached_template",
]
```

Rename `_validate_template_name` → `validate_template_name`. Update its call site(s) within this file.

- [ ] **Step 5: `models.py`, `config.py`, `sidecar.py`, `schema.py`, `github.py`, `cli_support.py`**

None of these six files have any underscore-prefixed module-level function (confirmed via the plan's research grep). Each still needs `__all__` added, listing its existing public names:

`models.py`: `__all__ = ["DevContainerConfig"]`

`config.py`: `__all__ = ["Settings", "load_settings"]`

`sidecar.py`: `__all__ = ["sidecar_path", "load_sidecar", "write_sidecar"]`

`schema.py`: `__all__ = ["validate_devcontainer_config"]`

`github.py`: `__all__ = ["list_template_names", "fetch_template"]`

`cli_support.py`: `__all__ = ["unwrap_or_exit"]`

Add each to its respective file, near the top, after the imports.

- [ ] **Step 6: Run the affected tests**

Run: `pixi run pytest tests/test_cli.py tests/test_cli_help.py tests/test_cli_version.py tests/test_ssh.py tests/test_build.py tests/test_store.py tests/test_models.py tests/test_config.py tests/test_sidecar.py tests/test_schema.py tests/test_github.py -v`
Expected: all pass, no import changes needed in any of these test files (confirmed via the plan's research grep).

- [ ] **Step 7: Grep for stale references**

Run: `grep -rn "_version_callback\|_root_callback\|_find_or_exit\|_BEGIN_MARKER\|_END_MARKER\|_dockerfile_stage_name\|_validate_template_name" src/ tests/`
Expected: zero results.

- [ ] **Step 8: Run the full suite and quality gate**

Run: `pixi run test unit`
Expected: same pass/skip counts as before this task, 0 new failures.

Run: `pixi run -e dev quality check`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add src/devtemplate/cli.py src/devtemplate/ssh.py src/devtemplate/build.py src/devtemplate/store.py src/devtemplate/models.py src/devtemplate/config.py src/devtemplate/sidecar.py src/devtemplate/schema.py src/devtemplate/github.py src/devtemplate/cli_support.py
git commit -m "refactor(dvt): explicit __all__ across the remaining flat modules

Final batch - every module in src/devtemplate now declares __all__
explicitly. Mechanical: drop underscore prefixes, no behavior change."
```

---

## Final Verification

After all 8 tasks:

- [ ] Run `grep -rn "^def _\|^class _\|^_[A-Za-z]" src/devtemplate --include="*.py"` and confirm zero results - no module-level underscore-prefixed name remains anywhere in `src/devtemplate`.
- [ ] Run `pixi run test unit` - full suite, same total pass/skip counts as this plan's starting point, 0 failures.
- [ ] Run `pixi run -e dev quality check` - clean.
- [ ] Confirm every package (`sshd/`, `features/`, `workspace/`, `commands/`, `pty/`) has a non-empty `__init__.py` with `__all__`, and `schemas/__init__.py` is still correctly empty (no Python code lives there).
