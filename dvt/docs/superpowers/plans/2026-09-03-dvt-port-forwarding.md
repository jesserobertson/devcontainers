# dvt host↔container port forwarding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `dvt` a way to reach a server running inside a workspace from the host — a dynamic `dvt forward` command plus `-L/--forward` flags on `dvt run` and `dvt ssh`, and declarative `appPort`/`forwardPorts` publishing at `dvt up` time.

**Architecture:** A self-contained stdlib forwarder (`devtemplate.forward`): the host binds a loopback TCP listener per `-L` spec; each accepted connection is bridged to a byte-relay spawned **inside** the container via `podman/docker exec -i` (`socat`/`ncat`/`nc`/`python3`, whichever the image has). This needs no host networking and no `dvt up --rebuild`. Separately, `run_container` learns to translate `appPort` + `forwardPorts` into published ports; the existing whole-dict config-drift check already forces a changed port set through `dvt up --rebuild`.

**Tech Stack:** Python 3.12+, Typer CLI, `logerr` Result types, docker-py (`client.containers.run`), stdlib `socket`/`subprocess`/`threading`/`os`. Tests: pytest, Hypothesis, `typer.testing.CliRunner`.

**Spec:** `dvt/docs/superpowers/specs/2026-09-03-dvt-port-forwarding-design.md`

## Global Constraints

- Python `>=3.12,<3.15`. **No new runtime dependencies** — `devtemplate.forward` is stdlib-only.
- Every fallible operation returns a `logerr` `Result` (`Ok`/`Err`), unwrapped at the CLI edge via `cli_support.unwrap_or_exit`. Follow the existing `@wrap_result` pattern.
- Modules under `src/devtemplate/` are import-time side-effect free and carry doctests where practical (`pytest` runs with `--doctest-modules` over `src/devtemplate`).
- Every module defines an explicit `__all__` (established repo convention).
- Raw byte pumps read/write **real file descriptors** with `os.read`/`os.write`, return on first-available bytes, and loop short writes — never buffered wrappers. See `src/devtemplate/sshd/stdio.py`'s docstring for the full rationale; reuse `CHUNK` from `devtemplate.pty`.
- Host listeners bind `127.0.0.1` by default (never `0.0.0.0` implicitly); container-side published ports bind `127.0.0.1` too.
- Works on Podman and Docker, on Windows/macOS/Linux hosts. No `--network=host`.
- Integration tests are marked `@pytest.mark.integration`, skip cleanly when no runtime is reachable, and are excluded from the default `pytest` run.
- Commit messages: Conventional Commits, and end every commit body with:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UggX6c6NeCd3AY7HD8Pggg
  ```

---

## File Structure

| File | Responsibility |
|---|---|
| `src/devtemplate/forward.py` | **new** — `ForwardSpec` (parse/render), relay-tool probe + per-tool relay argv, `PortForwarder` (listeners + per-connection `exec -i` bridge + teardown), `build_forwarder` Result factory, `block_forever` seam. |
| `src/devtemplate/cli.py` | **modify** — new `forward` command; `-L/--forward` option on `run` and `ssh`; wrap the exec calls so the tunnel is torn down on every unwind path. |
| `src/devtemplate/container.py` | **modify** — `translate_published_ports(config)`; pass `ports=` to `client.containers.run`; note published ports in the drift-check docstring. |
| `tests/test_forward.py` | **new** — unit tests for `ForwardSpec`, relay selection, `PortForwarder` round-trip + teardown, bind-conflict. |
| `tests/test_container.py` | **modify** — `translate_published_ports` cases; `run_container` passes the mapping through. |
| `tests/test_cli.py` | **modify** — `forward` command behavior; `-L` on `run`/`ssh` builds and tears down a forwarder. |
| `tests/integration/test_port_forward.py` | **new** — end-to-end dynamic forward + declarative publish/drift, against a real runtime. |
| `README.md` | **modify** — usage line + "Reaching a server inside a workspace" section. |
| `CHANGELOG.md` | **modify** — `## [Unreleased]` `Added` entry. |

---

## Task 1: `ForwardSpec` — parse and render `-L` specs

**Files:**
- Create: `src/devtemplate/forward.py`
- Test: `tests/test_forward.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ForwardSpec` — frozen dataclass, fields `bind: str`, `local: int`, `remote_host: str`, `remote: int`.
  - `ForwardSpec.parse(text: str) -> ForwardSpec` — classmethod, raises `ValueError` on malformed input.
  - `ForwardSpec.__str__` -> `"{bind}:{local}:{remote_host}:{remote}"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_forward.py`:

```python
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from devtemplate.forward import ForwardSpec


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2718", ForwardSpec("127.0.0.1", 2718, "localhost", 2718)),
        ("8080:3000", ForwardSpec("127.0.0.1", 8080, "localhost", 3000)),
        ("9000:db:5432", ForwardSpec("127.0.0.1", 9000, "db", 5432)),
        ("0.0.0.0:8080:db:5432", ForwardSpec("0.0.0.0", 8080, "db", 5432)),
    ],
)
def test_parse_accepts_the_four_forms(text, expected):
    assert ForwardSpec.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "  ", "a:b:c:d:e", "2718:db:notaport", "notaport", "8080:db:", "8080::5432"],
)
def test_parse_rejects_malformed_specs(text):
    with pytest.raises(ValueError) as exc:
        ForwardSpec.parse(text)
    assert repr(text.strip()) in str(exc.value) or text.strip() in str(exc.value)


@given(
    st.integers(1, 65535),
    st.integers(1, 65535),
    st.sampled_from(["localhost", "db", "127.0.0.1", "api.internal"]),
    st.sampled_from(["127.0.0.1", "0.0.0.0"]),
)
def test_str_round_trips_through_parse(local, remote, host, bind):
    spec = ForwardSpec(bind, local, host, remote)
    assert ForwardSpec.parse(str(spec)) == spec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_forward.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'devtemplate.forward'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/devtemplate/forward.py`:

```python
"""Host↔container TCP forwarding for dvt workspaces.

The `dvt ssh --stdio` transport is an asyncssh server that runs on the *host*
(bridging session channels to `podman`/`docker exec`), not an sshd inside the
container - so it cannot, by itself, reach the container's own localhost. This
module therefore forwards at the socket level: a loopback listener on the host,
and for every accepted connection a byte-relay spawned *inside* the container
via `exec -i` (`socat`/`ncat`/`nc`/`python3`, whichever the image provides).
No host networking, no container recreate.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ForwardSpec"]


@dataclass(frozen=True)
class ForwardSpec:
    """One `-L` mapping: listen on ``bind:local`` on the host, forward to
    ``remote_host:remote`` as reached from inside the container.

    Accepts (mirroring ``ssh -L``, plus a bare-port shorthand):

        >>> ForwardSpec.parse("2718")
        ForwardSpec(bind='127.0.0.1', local=2718, remote_host='localhost', remote=2718)
        >>> ForwardSpec.parse("8080:3000")
        ForwardSpec(bind='127.0.0.1', local=8080, remote_host='localhost', remote=3000)
        >>> ForwardSpec.parse("9000:db:5432")
        ForwardSpec(bind='127.0.0.1', local=9000, remote_host='db', remote=5432)
        >>> str(ForwardSpec.parse("0.0.0.0:8080:db:5432"))
        '0.0.0.0:8080:db:5432'
    """

    bind: str
    local: int
    remote_host: str
    remote: int

    @classmethod
    def parse(cls, text: str) -> ForwardSpec:
        raw = text.strip()
        fields = raw.split(":")

        def port(value: str) -> int:
            if not value.isdigit() or not (1 <= int(value) <= 65535):
                raise ValueError(f"invalid port {value!r} in forward spec {raw!r}")
            return int(value)

        if raw == "" or "" in fields:
            raise ValueError(f"empty field in forward spec {raw!r}")
        if len(fields) == 1:
            p = port(fields[0])
            return cls("127.0.0.1", p, "localhost", p)
        if len(fields) == 2:
            return cls("127.0.0.1", port(fields[0]), "localhost", port(fields[1]))
        if len(fields) == 3:
            return cls("127.0.0.1", port(fields[0]), fields[1], port(fields[2]))
        if len(fields) == 4:
            return cls(fields[0], port(fields[1]), fields[2], port(fields[3]))
        raise ValueError(
            f"forward spec {raw!r} has {len(fields)} colon-separated fields; "
            "expected LOCAL[:REMOTE_HOST:]REMOTE (1-4)"
        )

    def __str__(self) -> str:
        return f"{self.bind}:{self.local}:{self.remote_host}:{self.remote}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_forward.py "src/devtemplate/forward.py" -q --no-cov`
Expected: PASS (parametrized cases, the Hypothesis round-trip, and the module doctests).

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/forward.py tests/test_forward.py
git commit -m "feat(forward): parse -L port-forward specs"
```

---

## Task 2: Relay-tool probe and per-tool relay argv

**Files:**
- Modify: `src/devtemplate/forward.py`
- Test: `tests/test_forward.py`

**Interfaces:**
- Consumes: `ForwardSpec` (Task 1).
- Produces:
  - `RELAY_TOOLS: tuple[str, ...]` = `("socat", "ncat", "nc", "python3")`.
  - `select_relay_tool(probe_output: str) -> str | None` — first tool named (one per line, basename) in `probe_output`, else `None`.
  - `relay_argv(tool: str, spec: ForwardSpec) -> list[str]` — the `sh -c` payload argv `["sh", "-c", <snippet>]` that connects to `spec.remote_host:spec.remote` inside the container and relays stdin↔stdout. Raises `ValueError` for an unknown tool.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_forward.py`:

```python
from devtemplate.forward import relay_argv, select_relay_tool


@pytest.mark.parametrize(
    "probe, expected",
    [
        ("/usr/bin/socat\n/usr/bin/nc\n", "socat"),
        ("/bin/nc\n", "nc"),
        ("/usr/local/bin/ncat\n/usr/bin/python3\n", "ncat"),
        ("/usr/bin/python3\n", "python3"),
        ("", None),
        ("\n\n", None),
    ],
)
def test_select_relay_tool_picks_first_available(probe, expected):
    assert select_relay_tool(probe) == expected


def test_relay_argv_socat_targets_remote_host_and_port():
    spec = ForwardSpec("127.0.0.1", 2718, "localhost", 2718)
    argv = relay_argv("socat", spec)
    assert argv[:2] == ["sh", "-c"]
    assert "TCP:localhost:2718" in argv[2]


def test_relay_argv_nc_uses_host_then_port():
    spec = ForwardSpec("127.0.0.1", 9000, "db", 5432)
    assert "nc db 5432" in relay_argv("nc", spec)[2]


def test_relay_argv_python3_embeds_host_and_port():
    spec = ForwardSpec("127.0.0.1", 8080, "api.internal", 3000)
    snippet = relay_argv("python3", spec)[2]
    assert "api.internal" in snippet and "3000" in snippet
    assert snippet.startswith("exec python3 -c ")


def test_relay_argv_rejects_unknown_tool():
    with pytest.raises(ValueError):
        relay_argv("telnet", ForwardSpec("127.0.0.1", 1, "localhost", 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_forward.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'relay_argv'`.

- [ ] **Step 3: Write minimal implementation**

In `src/devtemplate/forward.py` add `import shlex` at the top, extend `__all__` with `"RELAY_TOOLS"`, `"select_relay_tool"`, `"relay_argv"`, and add:

```python
RELAY_TOOLS: tuple[str, ...] = ("socat", "ncat", "nc", "python3")

# Runs inside the container via `python3 -c`. Reads the exec's stdin (bytes
# from the host client) into a socket connected to the in-container target,
# and pumps the socket back to stdout, flushing every chunk. read1() returns
# on first-available data - correct for a live stream. Half-closes the socket
# when the host side hangs up. Real newlines + single-space indent so it is a
# valid script; `{host}`/`{port}` are substituted then the whole thing is
# shlex.quote'd by relay_argv.
_PYTHON_RELAY = "\n".join(
    [
        "import socket,sys,threading",
        "s=socket.create_connection(({host!r},{port}))",
        "def up():",
        " try:",
        "  while 1:",
        "   d=sys.stdin.buffer.read1(65536)",
        "   if not d: break",
        "   s.sendall(d)",
        " finally:",
        "  s.shutdown(socket.SHUT_WR)",
        "threading.Thread(target=up,daemon=True).start()",
        "while 1:",
        " d=s.recv(65536)",
        " if not d: break",
        " sys.stdout.buffer.write(d); sys.stdout.buffer.flush()",
    ]
)


def select_relay_tool(probe_output: str) -> str | None:
    """First entry of RELAY_TOOLS whose basename appears (one path per line)
    in `probe_output` - the stdout of a `command -v socat || command -v ncat
    || ...` run inside the container."""
    found = {line.rsplit("/", 1)[-1].strip() for line in probe_output.splitlines()}
    return next((tool for tool in RELAY_TOOLS if tool in found), None)


def relay_argv(tool: str, spec: ForwardSpec) -> list[str]:
    """`["sh", "-c", <snippet>]` that, run via `exec -i` in the container,
    connects to spec.remote_host:spec.remote and relays stdin<->stdout."""
    host, port = spec.remote_host, spec.remote
    if tool == "socat":
        snippet = f"exec socat - TCP:{shlex.quote(host)}:{port}"
    elif tool == "ncat":
        snippet = f"exec ncat {shlex.quote(host)} {port}"
    elif tool == "nc":
        snippet = f"exec nc {shlex.quote(host)} {port}"
    elif tool == "python3":
        script = _PYTHON_RELAY.format(host=host, port=port)
        snippet = f"exec python3 -c {shlex.quote(script)}"
    else:
        raise ValueError(f"unknown relay tool {tool!r}")
    return ["sh", "-c", snippet]
```

> **Note on `_PYTHON_RELAY`:** it is a real multi-line script (newline-joined
> list, single-space indent) passed to `python3 -c`, not a one-liner. Keep the
> behavior if you touch it: two directions, half-close the socket on stdin EOF,
> flush stdout every chunk. Step 5 compiles it as a gate.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_forward.py "src/devtemplate/forward.py" -q --no-cov`
Expected: PASS.

- [ ] **Step 5: Verify the python3 relay script is valid Python**

Add this test to `tests/test_forward.py`:

```python
def test_python_relay_script_compiles():
    from devtemplate.forward import _PYTHON_RELAY

    compile(_PYTHON_RELAY.format(host="db", port=5432), "<relay>", "exec")
```

Run: `pixi run pytest tests/test_forward.py::test_python_relay_script_compiles -q --no-cov`
Expected: PASS. If it raises `SyntaxError`, fix `_PYTHON_RELAY` before continuing.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/forward.py tests/test_forward.py
git commit -m "feat(forward): probe for and build an in-container byte relay"
```

---

## Task 3: `PortForwarder` + `build_forwarder` — listeners and the per-connection bridge

**Files:**
- Modify: `src/devtemplate/forward.py`
- Test: `tests/test_forward.py`

**Interfaces:**
- Consumes: `ForwardSpec`, `select_relay_tool`, `relay_argv` (Tasks 1-2); `find_workspace_container` from `devtemplate.container`; `CHUNK` from `devtemplate.pty`.
- Produces:
  - `build_forwarder(client, cli_binary: str, name: str, specs: list[str]) -> Result[PortForwarder, Exception]` — parses specs, resolves the running container, probes the relay tool, binds every listener, starts acceptor threads. On `Err` nothing is left open.
  - `PortForwarder.summary_lines() -> list[str]` — one `"127.0.0.1:2718 -> <name>:localhost:2718"` per spec.
  - `PortForwarder.close() -> None` — idempotent: closes listeners, terminates relay children, joins threads.
  - `block_forever() -> None` — blocks until `KeyboardInterrupt` (its own function so tests can monkeypatch it).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_forward.py`:

```python
import socket
import sys
import threading
from types import SimpleNamespace

import devtemplate.forward as forward_mod
from devtemplate.forward import build_forwarder


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _echo_server() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(
                target=lambda c: [c.sendall(d) for d in iter(lambda: c.recv(4096), b"")]
                and c.close(),
                args=(conn,),
                daemon=True,
            ).start()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


@pytest.fixture
def fake_container_env(monkeypatch):
    """Make build_forwarder resolve a running container and pick a relay tool
    whose 'exec -i' Popen is redirected to a local echo server instead of a
    real `podman exec`."""
    echo_srv, echo_port = _echo_server()
    monkeypatch.setattr(
        forward_mod, "find_workspace_container",
        lambda client, name: SimpleNamespace(name=f"dvt-{name}", status="running"),
    )
    # Probe: pretend the container has `socat`.
    monkeypatch.setattr(
        forward_mod, "_probe_relay_tool", lambda cli_binary, container: "socat"
    )
    real_popen = forward_mod.subprocess.Popen

    def fake_popen(argv, **kwargs):
        # argv == [cli_binary, "exec", "-i", container, "sh", "-c", snippet];
        # ignore it and just wire the pipes to the echo server via a plain
        # `python -c` TCP relay running on the host.
        relay = (
            "import socket,sys,threading;"
            f"s=socket.create_connection(('127.0.0.1',{echo_port}));"
            "threading.Thread(target=lambda:[s.sendall(x) for x in "
            "iter(lambda:sys.stdin.buffer.read1(4096),b'')] and s.shutdown(1),"
            "daemon=True).start();"
            "[ (sys.stdout.buffer.write(x),sys.stdout.buffer.flush()) for x in "
            "iter(lambda:s.recv(4096),b'')]"
        )
        return real_popen([sys.executable, "-c", relay], **kwargs)

    monkeypatch.setattr(forward_mod.subprocess, "Popen", fake_popen)
    yield
    echo_srv.close()


def test_build_forwarder_round_trips_bytes(fake_container_env):
    port = _free_port()
    fwd = build_forwarder(object(), "podman", "ws", [str(port)]).unwrap()
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(b"ping-through-tunnel")
        assert c.recv(4096) == b"ping-through-tunnel"
        c.close()
    finally:
        fwd.close()


def test_build_forwarder_close_is_idempotent(fake_container_env):
    fwd = build_forwarder(object(), "podman", "ws", [str(_free_port())]).unwrap()
    fwd.close()
    fwd.close()
    assert all(listener.fileno() == -1 for listener in fwd._listeners)


def test_build_forwarder_errs_when_container_not_running(monkeypatch):
    monkeypatch.setattr(forward_mod, "find_workspace_container", lambda c, n: None)
    result = build_forwarder(object(), "podman", "ws", ["2718"])
    assert result.is_err()
    assert "not running" in str(result.unwrap_err()) or "running" in str(result.unwrap_err())


def test_build_forwarder_errs_on_local_port_in_use(fake_container_env):
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    taken = busy.getsockname()[1]
    try:
        result = build_forwarder(object(), "podman", "ws", [str(taken)])
        assert result.is_err()
        assert str(taken) in str(result.unwrap_err())
    finally:
        busy.close()


def test_build_forwarder_errs_when_no_relay_tool(monkeypatch):
    monkeypatch.setattr(
        forward_mod, "find_workspace_container",
        lambda c, n: SimpleNamespace(name="dvt-ws", status="running"),
    )
    monkeypatch.setattr(forward_mod, "_probe_relay_tool", lambda cli_binary, container: None)
    result = build_forwarder(object(), "podman", "ws", ["2718"])
    assert result.is_err()
    assert "socat" in str(result.unwrap_err())
```

There is an unavoidable TOCTOU window between `_free_port()` closing its probe
socket and `build_forwarder` binding the same number — acceptable for a local
test; if it ever flakes in CI, wrap the round-trip test body in a 3× retry.

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_forward.py -q --no-cov`
Expected: FAIL — `ImportError: cannot import name 'build_forwarder'`.

- [ ] **Step 3: Write minimal implementation**

In `src/devtemplate/forward.py` add imports and the class. Top of file:

```python
import contextlib
import os
import socket
import subprocess
import threading

from logerr.utilities import wrap_result
from loguru import logger

from devtemplate.container import find_workspace_container
from devtemplate.pty import CHUNK
```

Extend `__all__` with `"PortForwarder"`, `"build_forwarder"`, `"block_forever"`.
Then:

```python
_PROBE = " || ".join(f"command -v {t}" for t in RELAY_TOOLS) + " || true"


def _probe_relay_tool(cli_binary: str, container: str) -> str | None:
    proc = subprocess.run(
        [cli_binary, "exec", container, "sh", "-c", _PROBE],
        capture_output=True, text=True, timeout=15,
    )
    return select_relay_tool(proc.stdout)


def block_forever() -> None:
    """Park until Ctrl-C. Its own function so tests can monkeypatch it."""
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


class PortForwarder:
    def __init__(
        self, cli_binary: str, container: str, name: str,
        specs: list[ForwardSpec], tool: str,
    ) -> None:
        self._cli_binary = cli_binary
        self._container = container
        self._name = name
        self._specs = specs
        self._tool = tool
        self._listeners: list[socket.socket] = []
        self._acceptors: list[threading.Thread] = []
        self._children: list[subprocess.Popen] = []
        self._conns: list[socket.socket] = []
        self._closed = False
        self._lock = threading.Lock()

    def _bind_all(self) -> None:
        for spec in self._specs:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind((spec.bind, spec.local))
            except OSError as exc:
                srv.close()
                self.close()
                raise OSError(
                    f"cannot forward {spec}: local port {spec.local} "
                    f"on {spec.bind} is unavailable ({exc})"
                ) from exc
            srv.listen(16)
            self._listeners.append(srv)
            thread = threading.Thread(
                target=self._accept_loop, args=(srv, spec), daemon=True
            )
            thread.start()
            self._acceptors.append(thread)

    def _accept_loop(self, srv: socket.socket, spec: ForwardSpec) -> None:
        while not self._closed:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(
                target=self._bridge, args=(conn, spec), daemon=True
            ).start()

    def _bridge(self, conn: socket.socket, spec: ForwardSpec) -> None:
        argv = [self._cli_binary, "exec", "-i", self._container, *relay_argv(self._tool, spec)]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as exc:
            logger.debug("forward {}: relay spawn failed: {}", spec, exc)
            conn.close()
            return
        with self._lock:
            if self._closed:
                proc.terminate(); conn.close(); return
            self._children.append(proc)
            self._conns.append(conn)

        assert proc.stdin is not None and proc.stdout is not None
        sock_fd, in_fd, out_fd = conn.fileno(), proc.stdin.fileno(), proc.stdout.fileno()

        def sock_to_proc() -> None:
            try:
                while True:
                    data = os.read(sock_fd, CHUNK)
                    if not data:
                        break
                    while data:
                        data = data[os.write(in_fd, data):]
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    proc.stdin.close()

        def proc_to_sock() -> None:
            try:
                while True:
                    data = os.read(out_fd, CHUNK)
                    if not data:
                        break
                    conn.sendall(data)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    conn.shutdown(socket.SHUT_WR)

        t1 = threading.Thread(target=sock_to_proc, daemon=True)
        t2 = threading.Thread(target=proc_to_sock, daemon=True)
        t1.start(); t2.start(); t2.join()
        proc.terminate()
        with contextlib.suppress(OSError):
            conn.close()

    def summary_lines(self) -> list[str]:
        return [
            f"{spec.bind}:{spec.local} -> {self._name}:{spec.remote_host}:{spec.remote}"
            for spec in self._specs
        ]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for srv in self._listeners:
            with contextlib.suppress(OSError):
                srv.close()
        for proc in list(self._children):
            with contextlib.suppress(Exception):
                proc.terminate()
        for conn in list(self._conns):
            with contextlib.suppress(OSError):
                conn.close()
        for thread in self._acceptors:
            thread.join(timeout=2.0)
```

Then the factory:

```python
@wrap_result
def build_forwarder(
    client: object, cli_binary: str, name: str, specs: list[str]
) -> PortForwarder:
    """Parse `specs`, resolve the running workspace container, probe its relay
    tool, and bind every listener. On failure nothing is left open.

    Decorated with @wrap_result: a bare return becomes Ok(...), a raised
    exception becomes Err(...) - the same convention as
    devtemplate.ssh.stdio_proxy.
    """
    parsed = [ForwardSpec.parse(s) for s in specs]
    if not parsed:
        raise ValueError("no forward specs given")
    container = find_workspace_container(client, name)
    if container is None or container.name is None:
        raise ValueError(f"No workspace named {name!r} is running.")
    tool = _probe_relay_tool(cli_binary, container.name)
    if tool is None:
        raise ValueError(
            f"workspace {name!r} has none of {', '.join(RELAY_TOOLS)} installed - "
            "dvt needs one of them inside the container to relay a forwarded "
            "connection. Install one in the image, or publish the port "
            "declaratively via devcontainer.json \"appPort\" and `dvt up --rebuild`."
        )
    fwd = PortForwarder(cli_binary, container.name, name, parsed, tool)
    fwd._bind_all()
    return fwd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_forward.py "src/devtemplate/forward.py" -q --no-cov`
Expected: PASS — byte round-trip, idempotent close, and all three error paths.

- [ ] **Step 5: Full unit run + typecheck + lint**

Run: `pixi run pytest tests/test_forward.py -q` then `pixi run quality check`
Expected: PASS; mypy and ruff clean on `forward.py`.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/forward.py tests/test_forward.py
git commit -m "feat(forward): PortForwarder listeners + per-connection exec bridge"
```

---

## Task 4: `dvt forward` command

**Files:**
- Modify: `src/devtemplate/cli.py` (imports near line 29-36; new command after `run`, ~line 293)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_forwarder`, `block_forever` from `devtemplate.forward`; existing `get_client`, `load_settings`, `resolve_existing`, `unwrap_or_exit`, `console`.
- Produces: `dvt forward [-n NAME] SPEC...` — foreground; prints mappings, blocks until Ctrl-C, tears down, prints `Stopped forwarding.`, exits 0.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def _stub_forward_deps(monkeypatch, cli_module, *, build_result):
    monkeypatch.setattr(
        cli_module, "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok(name or "inferred"),
    )
    monkeypatch.setattr(cli_module, "build_forwarder", lambda *a, **k: build_result)
    monkeypatch.setattr(cli_module, "block_forever", lambda: None)


def test_forward_prints_mappings_and_tears_down(monkeypatch):
    import devtemplate.cli as cli_module

    closed = {"n": 0}
    fake_fwd = SimpleNamespace(
        summary_lines=lambda: ["127.0.0.1:2718 -> web:localhost:2718"],
        close=lambda: closed.__setitem__("n", closed["n"] + 1),
    )
    _stub_forward_deps(monkeypatch, cli_module, build_result=cli_module.Ok(fake_fwd))

    result = runner.invoke(cli_module.app, ["forward", "-n", "web", "2718"])

    assert result.exit_code == 0, result.output
    assert "127.0.0.1:2718 -> web:localhost:2718" in result.output
    assert "Stopped forwarding." in result.output
    assert closed["n"] == 1


def test_forward_reports_setup_failure(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_forward_deps(
        monkeypatch, cli_module,
        build_result=cli_module.Err(ValueError("local port 2718 is unavailable")),
    )

    result = runner.invoke(cli_module.app, ["forward", "-n", "web", "2718"])

    assert result.exit_code == 1
    assert "2718" in result.output


def test_forward_requires_at_least_one_spec(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_forward_deps(monkeypatch, cli_module, build_result=cli_module.Ok(None))
    result = runner.invoke(cli_module.app, ["forward", "-n", "web"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_cli.py -q -k forward --no-cov`
Expected: FAIL — `forward` command not registered / `build_forwarder` not on `cli_module`.

- [ ] **Step 3: Write minimal implementation**

In `src/devtemplate/cli.py`, add to the `devtemplate.workspace`/forward imports:

```python
from devtemplate.forward import block_forever, build_forwarder
```

Add the command (after `run`):

```python
@app.command()
def forward(
    specs: list[str] = typer.Argument(  # noqa: B008
        ...,
        metavar="SPEC...",
        help="Port forward(s), each LOCAL[:REMOTE_HOST:]REMOTE "
        "(default REMOTE_HOST=localhost, LOCAL=REMOTE). Repeatable: "
        "'dvt forward -n web 2718 8080:3000'.",
    ),
    name: str | None = typer.Option(  # noqa: B008
        None, "--name", "-n",
        help="Workspace to forward into (default: inferred from the current folder).",
    ),
) -> None:
    """Forward host ports to a server running inside a workspace, over the
    existing `dvt ssh` transport - no container rebuild, no host networking.

    Runs in the foreground until interrupted (Ctrl-C). Handy for a dev server
    started with `dvt run`, e.g. `marimo edit --port 2718` reachable at
    http://localhost:2718 on the host.
    """
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
        resolve_existing(handle.client, name, Path.cwd(), "forward"), console
    )
    forwarder = unwrap_or_exit(
        build_forwarder(handle.client, handle.cli_binary, resolved_name, specs), console
    )
    for line in forwarder.summary_lines():
        console.print(line)
    console.print("[dim]Forwarding until interrupted (Ctrl-C to stop).[/dim]")
    try:
        block_forever()
    finally:
        forwarder.close()
    console.print("Stopped forwarding.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_cli.py -q -k forward --no-cov`
Expected: PASS (all three).

- [ ] **Step 5: Verify `--describe` and `--help` pick it up**

Run: `pixi run pytest tests/test_cli_describe.py tests/test_cli_help.py -q` then
`python -m devtemplate.cli forward --describe`
Expected: existing describe/help suites still pass; the manual `--describe` emits
a manifest entry for `forward` with `specs` (argument) and `--name` (option).

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'dvt forward' command"
```

---

## Task 5: `-L/--forward` on `dvt run` and `dvt ssh`

**Files:**
- Modify: `src/devtemplate/cli.py` (`run` ~lines 247-292; `ssh` ~lines 202-244)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_forwarder` (Task 3), already imported (Task 4).
- Produces: `run` and `ssh` each gain `forward: list[str]` via `-L`/`--forward` (repeatable). When non-empty, a `PortForwarder` is built before the exec and `.close()`d in a `finally`, so the tunnel dies with the command on every path (normal exit, non-zero, Ctrl-C, build error). `ssh --stdio` ignores `-L`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_run_with_dash_L_builds_and_closes_a_forwarder(monkeypatch):
    import devtemplate.cli as cli_module

    captured = _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))
    events: list[str] = []
    fake_fwd = SimpleNamespace(
        summary_lines=lambda: [], close=lambda: events.append("closed")
    )

    def fake_build(client, cli_binary, name, specs):
        events.append(f"built:{specs}")
        return cli_module.Ok(fake_fwd)

    monkeypatch.setattr(cli_module, "build_forwarder", fake_build)

    result = runner.invoke(
        cli_module.app, ["run", "-n", "web", "-L", "2718", "python", "-m", "http.server"]
    )

    assert result.exit_code == 0, result.output
    assert events == ["built:['2718']", "closed"]
    assert captured["command"] == ["python", "-m", "http.server"]


def test_run_forwarder_closed_even_when_command_fails(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(7))
    closed = {"n": 0}
    monkeypatch.setattr(
        cli_module, "build_forwarder",
        lambda *a, **k: cli_module.Ok(
            SimpleNamespace(summary_lines=lambda: [],
                            close=lambda: closed.__setitem__("n", closed["n"] + 1))
        ),
    )

    result = runner.invoke(cli_module.app, ["run", "-n", "web", "-L", "2718", "false"])

    assert result.exit_code == 7
    assert closed["n"] == 1


def test_run_dash_L_build_failure_exits_one(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))
    monkeypatch.setattr(
        cli_module, "build_forwarder",
        lambda *a, **k: cli_module.Err(ValueError("port 2718 unavailable")),
    )
    result = runner.invoke(cli_module.app, ["run", "-n", "web", "-L", "2718", "true"])
    assert result.exit_code == 1
    assert "2718" in result.output


def test_ssh_with_dash_L_builds_and_closes_a_forwarder(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module, "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok(name),
    )
    monkeypatch.setattr(
        cli_module, "exec_interactive",
        lambda cli_binary, client, name: cli_module.Ok(0),
    )
    closed = {"n": 0}
    monkeypatch.setattr(
        cli_module, "build_forwarder",
        lambda *a, **k: cli_module.Ok(
            SimpleNamespace(summary_lines=lambda: [],
                            close=lambda: closed.__setitem__("n", closed["n"] + 1))
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh", "web", "-L", "2718"])

    assert result.exit_code == 0, result.output
    assert closed["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_cli.py -q -k "dash_L" --no-cov`
Expected: FAIL — `-L` is an unknown option to `run`/`ssh`.

- [ ] **Step 3: Write minimal implementation**

`run` — add the option and wrap the exec. New parameter (after `tty`):

```python
    forward: list[str] = typer.Option(  # noqa: B008
        [],
        "--forward",
        "-L",
        help="Forward a host port to a server inside the workspace for the "
        "lifetime of this command, e.g. -L 2718 (repeatable). Spec: "
        "LOCAL[:REMOTE_HOST:]REMOTE.",
    ),
```

Replace the final exec/exit block of `run` with:

```python
    forwarder = (
        unwrap_or_exit(
            build_forwarder(handle.client, handle.cli_binary, resolved_name, forward),
            console,
        )
        if forward
        else None
    )
    try:
        exit_code = unwrap_or_exit(
            exec_command(
                handle.cli_binary, handle.client, resolved_name, command, tty=tty
            ),
            console,
        )
    finally:
        if forwarder is not None:
            forwarder.close()
    raise typer.Exit(code=exit_code)
```

`ssh` — add the same `forward` option parameter. Leave the `--stdio` branch
untouched; wrap only the interactive path:

```python
    if stdio:
        exit_code = unwrap_or_exit(
            stdio_proxy(handle.cli_binary, handle.client, resolved_name), errors
        )
        raise typer.Exit(code=exit_code)

    forwarder = (
        unwrap_or_exit(
            build_forwarder(handle.client, handle.cli_binary, resolved_name, forward),
            errors,
        )
        if forward
        else None
    )
    try:
        exit_code = unwrap_or_exit(
            exec_interactive(handle.cli_binary, handle.client, resolved_name), errors
        )
    finally:
        if forwarder is not None:
            forwarder.close()
    raise typer.Exit(code=exit_code)
```

(This replaces the current `result = (... if stdio else ...)` / `exit_code =
unwrap_or_exit(...)` / `raise typer.Exit` tail of `ssh`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_cli.py -q -k "dash_L or run_ or ssh_" --no-cov`
Expected: PASS — new `-L` tests plus the pre-existing `run`/`ssh` tests
(unchanged behavior when no `-L`).

- [ ] **Step 5: Full CLI suite + quality**

Run: `pixi run pytest tests/test_cli.py tests/test_cli_describe.py tests/test_cli_help.py -q`
then `pixi run quality check`
Expected: PASS; clean.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/cli.py tests/test_cli.py
git commit -m "feat(cli): -L/--forward on 'dvt run' and 'dvt ssh'"
```

---

## Task 6: Declarative — publish `appPort` + `forwardPorts` at `dvt up`

**Files:**
- Modify: `src/devtemplate/container.py` (`__all__` ~line 22; new helper near `translate_run_args` ~line 157; `run_container` `client.containers.run(...)` call ~lines 217-229; `config_has_drifted` docstring ~line 123)
- Test: `tests/test_container.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `translate_published_ports(config: dict) -> dict[str, tuple[str, int]]` — docker-py `ports=` mapping (`{"2718/tcp": ("127.0.0.1", 2718)}`) built from `appPort` and `forwardPorts`. `run_container` passes it as `ports=`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_container.py`:

```python
from devtemplate.container import translate_published_ports


@pytest.mark.parametrize(
    "config, expected",
    [
        ({}, {}),
        ({"appPort": 2718}, {"2718/tcp": ("127.0.0.1", 2718)}),
        ({"appPort": [2718, 8080]},
         {"2718/tcp": ("127.0.0.1", 2718), "8080/tcp": ("127.0.0.1", 8080)}),
        ({"appPort": "9000:3000"}, {"3000/tcp": ("127.0.0.1", 9000)}),
        ({"forwardPorts": [2718]}, {"2718/tcp": ("127.0.0.1", 2718)}),
        ({"forwardPorts": ["8080:3000"]}, {"3000/tcp": ("127.0.0.1", 8080)}),
        ({"appPort": [2718], "forwardPorts": [9229]},
         {"2718/tcp": ("127.0.0.1", 2718), "9229/tcp": ("127.0.0.1", 9229)}),
    ],
)
def test_translate_published_ports(config, expected):
    assert translate_published_ports(config) == expected


def test_translate_published_ports_rejects_label_form():
    with pytest.raises(ValueError):
        translate_published_ports({"forwardPorts": ["app:3000"]})


def test_run_container_publishes_translated_ports(tmp_path):
    config = {**FASTAPI_CONFIG, "appPort": [2718]}
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    run_container(
        fake_client, "img", config, "web", tmp_path, tmp_path / "devcontainer.json"
    )

    _, kwargs = fake_client.containers.run.call_args
    assert kwargs["ports"] == {"2718/tcp": ("127.0.0.1", 2718)}


def test_run_container_omits_ports_when_none_declared(tmp_path):
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()
    run_container(
        fake_client, "img", FASTAPI_CONFIG, "web", tmp_path,
        tmp_path / "devcontainer.json",
    )
    _, kwargs = fake_client.containers.run.call_args
    assert kwargs["ports"] == {}
```

(If `FASTAPI_CONFIG` in this file already sets `appPort`/`forwardPorts`, use a
minimal `{"image": "x:1"}` config for the last two instead.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_container.py -q -k "published_ports or publishes_translated" --no-cov`
Expected: FAIL — `ImportError: cannot import name 'translate_published_ports'`.

- [ ] **Step 3: Write minimal implementation**

In `src/devtemplate/container.py`, add `"translate_published_ports"` to
`__all__`, then add:

```python
def _publish_entry(value: object) -> tuple[int, int]:
    """One appPort/forwardPorts item -> (host_port, container_port). Accepts an
    int (same port both sides) or a 'HOST:CONTAINER' string. A non-numeric
    segment (the devcontainer 'label:port' UI form) is refused - it has no
    bind meaning; use `dvt forward` / `-L` for named forwards."""
    if isinstance(value, int):
        return value, value
    if isinstance(value, str) and ":" in value:
        host, _, container = value.partition(":")
        if host.isdigit() and container.isdigit():
            return int(host), int(container)
    if isinstance(value, str) and value.isdigit():
        return int(value), int(value)
    raise ValueError(
        f"can't publish port {value!r} - expected an int or 'HOST:CONTAINER'"
    )


def translate_published_ports(config: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """Translate devcontainer.json `appPort` (hard publish, per spec) and
    `forwardPorts` into docker-py's `ports=` mapping, each bound to host
    loopback. Empty when neither key is set - byte-for-byte the previous
    behavior. Changing either key changes devcontainer.json, which
    `config_has_drifted` already catches, so `dvt up` refuses a stale port
    set and points at `--rebuild` with no extra check here."""
    raw = config.get("appPort", [])
    app_ports = raw if isinstance(raw, list) else [raw]
    forward_ports = config.get("forwardPorts", [])

    mapping: dict[str, tuple[str, int]] = {}
    for item in [*app_ports, *forward_ports]:
        host_port, container_port = _publish_entry(item)
        mapping[f"{container_port}/tcp"] = ("127.0.0.1", host_port)
    return mapping
```

In `run_container`, add to the `client.containers.run(...)` kwargs:

```python
        ports=translate_published_ports(config),
```

In `config_has_drifted`'s docstring, add a sentence: *"This whole-dict compare
also covers published ports (`appPort`/`forwardPorts`): change either and
`dvt up` refuses until `--rebuild`, since ports can only be set at create
time."*

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_container.py -q` then `pixi run quality check`
Expected: PASS; clean. (The existing `run_container` tests now also see
`ports={}` — confirm none assert the exact kwarg set; if one does, update it to
allow `ports`.)

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/container.py tests/test_container.py
git commit -m "feat(up): publish appPort/forwardPorts from devcontainer.json"
```

---

## Task 7: Integration tests — real runtime, end to end

**Files:**
- Create: `tests/integration/test_port_forward.py`

**Interfaces:**
- Consumes: `devtemplate.cli.app`, `devtemplate.runtime.get_client`,
  `devtemplate.forward.build_forwarder`.
- Produces: nothing (test-only).

- [ ] **Step 1: Write the tests**

Create `tests/integration/test_port_forward.py`:

```python
"""Real-runtime host<->container port-forwarding integration tests.

Opt-in only (`pixi run test integration`); skips cleanly when no Docker/Podman
engine is reachable. Mirrors tests/integration/test_native_runtime_lifecycle.py.
"""

from __future__ import annotations

import http.client
import json
import time
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.forward import build_forwarder
from devtemplate.runtime import get_client

runner = CliRunner()
pytestmark = pytest.mark.integration
runtime_unreachable = get_client("auto").is_err()

CONNECTOR_IMAGE = "python:3.12-alpine"  # ships python3; busybox `nc` also present


def _project(tmp_path: Path, extra: dict) -> Path:
    d = tmp_path / ".devcontainer"
    d.mkdir()
    (d / "devcontainer.json").write_text(
        json.dumps({"name": "dvt-fwd-test", "image": CONNECTOR_IMAGE, **extra})
    )
    return tmp_path


def _get(port: int, tries: int = 40) -> str:
    last = None
    for _ in range(tries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/")
            body = conn.getresponse().read().decode(errors="replace")
            conn.close()
            return body
        except OSError as exc:  # not up yet
            last = exc
            time.sleep(0.25)
    raise AssertionError(f"nothing answered on 127.0.0.1:{port}: {last}")


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_dvt_forward_reaches_an_in_container_server(tmp_path, monkeypatch):
    ws = f"dvt-fwd-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(_project(tmp_path, {}))
    handle = get_client("auto").unwrap()
    try:
        assert runner.invoke(app, ["up", ws]).exit_code == 0
        # Leave an HTTP server listening on :2718 inside the container.
        started = runner.invoke(
            app,
            ["run", "-n", ws, "sh", "-c",
             "(python3 -m http.server 2718 >/dev/null 2>&1 &) ; sleep 1"],
        )
        assert started.exit_code == 0, started.output

        fwd = build_forwarder(handle.client, handle.cli_binary, ws, ["2718"]).unwrap()
        try:
            body = _get(2718)
            assert "Directory listing" in body
        finally:
            fwd.close()
    finally:
        runner.invoke(app, ["delete", ws])


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_appPort_is_published_and_drift_demands_rebuild(tmp_path, monkeypatch):
    ws = f"dvt-appport-{uuid.uuid4().hex[:8]}"
    project = _project(tmp_path, {"appPort": [2719],
                                  "postStartCommand":
                                  "python3 -m http.server 2719 >/dev/null 2>&1 &"})
    monkeypatch.chdir(project)
    try:
        assert runner.invoke(app, ["up", ws]).exit_code == 0
        assert "Directory listing" in _get(2719)

        cfg = project / ".devcontainer" / "devcontainer.json"
        cfg.write_text(cfg.read_text().replace("2719", "2720"))
        drifted = runner.invoke(app, ["up", ws])
        assert drifted.exit_code != 0
        assert "--rebuild" in drifted.output
    finally:
        runner.invoke(app, ["delete", ws])
```

- [ ] **Step 2: Run the integration tests against a real runtime**

Run: `pixi run test integration` (or
`pixi run pytest tests/integration/test_port_forward.py -q -m integration`)
Expected: both PASS on a machine with Podman/Docker; SKIP (not fail) without one.

- [ ] **Step 3: Confirm they don't run in the default suite**

Run: `pixi run pytest tests/integration/test_port_forward.py -q`
Expected: `2 deselected` (the default `-m "not integration"` filter).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_port_forward.py
git commit -m "test(integration): forwarded port answers from the host"
```

---

## Task 8: Docs — README, CHANGELOG, help copy

**Files:**
- Modify: `README.md` (usage block ~lines 16-25; new section after it)
- Modify: `CHANGELOG.md` (new `## [Unreleased]` above `## [0.4.1]` ~line 11)

**Interfaces:** none (docs only).

- [ ] **Step 1: README — usage line + new section**

In the `## Usage` block, after the `dvt run ...` line add:

```
    dvt forward -n my-project 2718   # reach an in-container :2718 server at http://localhost:2718
```

After the `## Usage` block, add:

```markdown
## Reaching a server inside a workspace

A workspace's own network isn't routable from the host, so a server you start
inside one (a dev server, a notebook) isn't reachable at `localhost` until you
forward its port.

**Dynamically (no rebuild).** `dvt forward` tunnels one or more ports over the
same transport `dvt ssh` uses, until you Ctrl-C it:

    dvt forward -n my-project 2718            # localhost:2718 -> container localhost:2718
    dvt forward -n my-project 8080:3000       # localhost:8080 -> container localhost:3000
    dvt forward -n my-project 9000:db:5432    # localhost:9000 -> db:5432 from inside

Or bind the tunnel to the lifetime of a command:

    dvt run -n my-project -L 2718 just viz-notebooks   # marimo edit --port 2718, reachable while it runs
    dvt ssh my-project -L 8888                          # tunnel stays up for the shell session

The workspace image needs one of `socat`, `ncat`, `nc`, or `python3` on `PATH`
(the relay runs inside the container). `-L`/`--forward` is repeatable.

**Declaratively (at `dvt up`).** `appPort` and `forwardPorts` in
`devcontainer.json` are published to the host when the container is created:

```json
{ "image": "...", "appPort": [2718] }
```

Because published ports are fixed at creation, changing them makes the next
`dvt up` stop and ask for `dvt up --rebuild` rather than silently recreating
the container.
```

- [ ] **Step 2: CHANGELOG — Unreleased entry**

Insert above `## [0.4.1] - 2026-09-01`:

```markdown
## [Unreleased]

### Added

- Host↔container port forwarding. `dvt forward -n <ws> <spec>...` tunnels host
  ports to servers running inside a workspace over the existing `dvt ssh`
  transport — no container rebuild, no host-networking assumption, works on
  Podman and Docker. `dvt run` and `dvt ssh` take a repeatable `-L/--forward
  <spec>` that lives for the command's / session's lifetime (e.g. `dvt run -L
  2718 just viz-notebooks` to reach `marimo edit --port 2718` at
  `http://localhost:2718`). `<spec>` is `LOCAL[:REMOTE_HOST:]REMOTE`
  (`REMOTE_HOST` defaults to `localhost`, `LOCAL` to `REMOTE`). Needs one of
  `socat`/`ncat`/`nc`/`python3` inside the container.
- `dvt up` now publishes `appPort` and `forwardPorts` from `devcontainer.json`
  to the host (bound to loopback). Since published ports are fixed at container
  creation, a changed set makes `dvt up` ask for `--rebuild` rather than
  recreating silently.
```

- [ ] **Step 3: Build the docs**

Run: `pixi run docs build --strict`
Expected: builds clean (no broken-link / strict warnings from the new section).

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: document dvt port forwarding (forward, -L, appPort)"
```

---

## Task 9: Final verification

**Files:** none (verification only).

- [ ] **Step 1: Full unit suite + quality gate**

Run: `pixi run check-all`
Expected: `test all` + `quality check` both PASS (mypy, ruff lint + format).

- [ ] **Step 2: Integration suite on a real runtime**

Run: `pixi run test integration`
Expected: the two new tests in `test_port_forward.py` PASS alongside the
existing lifecycle test (or all SKIP if no runtime).

- [ ] **Step 3: Manual smoke (the spec's acceptance check)**

```bash
mkdir -p /tmp/fwd-smoke/.devcontainer
echo '{"name":"fwd-smoke","image":"python:3.12-alpine"}' > /tmp/fwd-smoke/.devcontainer/devcontainer.json
cd /tmp/fwd-smoke
dvt up fwd-smoke
dvt run -n fwd-smoke sh -c '(python3 -m http.server 2718 &) ; sleep 1'
dvt forward -n fwd-smoke 2718 &   # or a second terminal
sleep 2 && curl -sS localhost:2718 | head -c 200
kill %1 ; dvt delete fwd-smoke
```
Expected: `curl` prints the directory-listing HTML from inside the container.

- [ ] **Step 4: `--describe` surface check**

Run: `python -m devtemplate.cli --describe | python -m json.tool | grep -A6 '"forward"'`
Expected: `forward` present with `specs` argument and `--name` option; `run` and
`ssh` each list a `--forward` option.

- [ ] **Step 5: Confirm the branch is clean and commits are scoped**

Run: `git log --oneline main..HEAD` and `git status`
Expected: 8 focused commits (Tasks 1-8), clean tree.

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| `ForwardSpec` grammar `LOCAL[:REMOTE_HOST:]REMOTE` + shorthand | Task 1 |
| In-container relay, tool probe (`socat`/`ncat`/`nc`/`python3`), hard error if none | Task 2, Task 3 (`build_forwarder`) |
| `PortForwarder`: loopback listeners, per-conn `exec -i` bridge, raw-fd pumps, idempotent teardown | Task 3 |
| `dvt forward [-n NAME] SPEC...`, foreground, Ctrl-C → clean stop, exit 0 | Task 4 |
| Setup failures (bad spec, port in use, not running, no relay) → exit 1 | Task 3 (Err) + Task 4/5 (`unwrap_or_exit`) |
| Repeatable `-L/--forward` on `dvt run` (command-lifetime) and `dvt ssh` (session-lifetime); `--stdio` ignores it | Task 5 |
| Tunnel torn down on every unwind path | Task 5 (`finally`) |
| Declarative `appPort` + `forwardPorts` → published ports, loopback bound | Task 6 |
| `forwardPorts` `label:port` form rejected with a pointer to `-L` | Task 6 (`_publish_entry`) |
| Drift already steers a changed port set to `--rebuild`; no second check; docstring note | Task 6 |
| Podman + Docker, Windows/macOS, no `--network=host` | docker-py `ports=` (Task 6); forwarder is socket-level (Task 3) |
| Unit tests: spec parse (+ Hypothesis round-trip), relay selection, forwarder round-trip + teardown, bind conflict | Tasks 1-3 |
| Unit tests: `translate_published_ports`, `run_container` passes `ports=` | Task 6 |
| Integration: listener in a throwaway workspace, forwarded port answers from host; declarative publish + drift | Task 7 |
| README + CHANGELOG + `--describe`/`--help`, marimo use case called out | Task 8 (+ Task 4/5 help strings) |
| No new runtime deps | Global Constraints; `forward.py` stdlib-only |

**Placeholder scan:** none — every step carries real code or a concrete command. The two "Note:" callouts (Task 2 `_PYTHON_RELAY`, Task 3 `Ok(...)` convention) point at an existing file to check, not deferred work.

**Type consistency:** `build_forwarder(client, cli_binary, name, specs) -> Result[PortForwarder, Exception]` used identically in Tasks 4 and 5. `PortForwarder.summary_lines()`/`.close()` match between Task 3 (def), Task 4 and Task 5 (fakes). `translate_published_ports(config) -> dict[str, tuple[str, int]]` consistent between Task 6 def and its `run_container` call site. `ForwardSpec` field order `(bind, local, remote_host, remote)` consistent across Tasks 1-3. `block_forever` defined in Task 3, imported/monkeypatched in Task 4.

**Known follow-ups (not blockers):** the `_PYTHON_RELAY` one-liner is the fiddliest artifact here — Task 2 Step 5 explicitly compiles it as a gate. If it proves brittle, the fallback ordering (`socat`/`ncat`/`nc` first) means most real images never reach it.
