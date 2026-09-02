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

import contextlib
import os
import shlex
import socket
import subprocess
import threading
from dataclasses import dataclass

from docker.client import DockerClient
from logerr.utilities import wrap_result
from loguru import logger

from devtemplate.container import find_workspace_container
from devtemplate.pty import CHUNK

__all__ = [
    "ForwardSpec",
    "RELAY_TOOLS",
    "select_relay_tool",
    "relay_argv",
    "PortForwarder",
    "build_forwarder",
    "block_forever",
]


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


_PROBE = " || ".join(f"command -v {t}" for t in RELAY_TOOLS) + " || true"


def _probe_relay_tool(cli_binary: str, container: str) -> str | None:
    proc = subprocess.run(
        [cli_binary, "exec", container, "sh", "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=15,
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
        self,
        cli_binary: str,
        container: str,
        name: str,
        specs: list[ForwardSpec],
        tool: str,
    ) -> None:
        self._cli_binary = cli_binary
        self._container = container
        self._name = name
        self._specs = specs
        self._tool = tool
        self._listeners: list[socket.socket] = []
        self._acceptors: list[threading.Thread] = []
        self._children: list[subprocess.Popen[bytes]] = []
        self._conns: list[socket.socket] = []
        self._closed = False
        self._lock = threading.Lock()

    def _bind_all(self) -> None:
        for spec in self._specs:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name != "nt":
                # POSIX SO_REUSEADDR only frees a TIME_WAIT port; on Windows it
                # instead lets a second bind steal a port that is actively in
                # use, which would silently swallow the "local port unavailable"
                # error path below. Windows' default (no opt) already gives the
                # exclusive bind we want.
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
        argv = [
            self._cli_binary,
            "exec",
            "-i",
            self._container,
            *relay_argv(self._tool, spec),
        ]
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        except OSError as exc:
            logger.debug("forward {}: relay spawn failed: {}", spec, exc)
            conn.close()
            return
        with self._lock:
            if self._closed:
                proc.terminate()
                conn.close()
                return
            self._children.append(proc)
            self._conns.append(conn)

        assert proc.stdin is not None and proc.stdout is not None
        proc_stdin, proc_stdout = proc.stdin, proc.stdout
        in_fd, out_fd = proc_stdin.fileno(), proc_stdout.fileno()

        def sock_to_proc() -> None:
            try:
                while True:
                    # conn.recv(), not os.read(conn.fileno()): a socket fd is
                    # not an os.read-able descriptor on Windows (raises
                    # EBADF). The subprocess-pipe side keeps os.read/os.write.
                    # Same split as devtemplate.sshd.stdio.pump_stdio_to_socket.
                    data = conn.recv(CHUNK)
                    if not data:
                        break
                    while data:
                        data = data[os.write(in_fd, data) :]
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    proc_stdin.close()

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
        t1.start()
        t2.start()
        t2.join()
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


@wrap_result
def build_forwarder(
    client: DockerClient, cli_binary: str, name: str, specs: list[str]
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
            'declaratively via devcontainer.json "appPort" and `dvt up --rebuild`.'
        )
    fwd = PortForwarder(cli_binary, container.name, name, parsed, tool)
    fwd._bind_all()
    return fwd
