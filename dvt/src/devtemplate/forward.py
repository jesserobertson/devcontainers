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

import shlex
from dataclasses import dataclass

__all__ = ["ForwardSpec", "RELAY_TOOLS", "select_relay_tool", "relay_argv"]


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
