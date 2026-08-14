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
            exit_codes.append(await handle_process(process, cli_binary, container_name))
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
