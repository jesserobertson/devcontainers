"""A real (if minimal) SSH server for `dvt ssh --stdio <name>`.

`ProxyCommand` only replaces the *transport* an SSH client talks over - the
client still performs a full SSH protocol exchange (version banner, key
exchange, authentication, channel open) across it. Piping straight to a bare
`docker exec -i <name> sh` therefore cannot work: a shell cannot speak SSH.

This module runs an actual `asyncssh` server bound to this process's own
stdin/stdout (via an internal `socket.socketpair()` bridged by blocking-I/O
threads), and forwards each session it accepts to `docker`/`podman exec -i
<container> sh`. That is what makes `dvt ssh --stdio` usable as a
`ProxyCommand` target - by JetBrains Gateway, VS Code Remote-SSH, or plain
`ssh` - without baking an `sshd` into every workspace image.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import threading

import asyncssh

_CHUNK = 4096
"""Read size for every byte pump in this module - a session is interactive
terminal traffic, so throughput never matters, but latency does."""

_BRIDGE_DRAIN_TIMEOUT = 5.0
"""How long to wait, after the event loop has finished, for the stdio bridge
threads to notice the server socket closed and flush the last of their output.
The wait is a plain `Thread.join` outside the loop, and the threads are daemons,
so exceeding it costs nothing beyond the lost tail of that output."""

_EXIT_SESSION_FAILED = 255
"""Exit code for a session that never got off the ground - `docker`/`podman`
missing from PATH, container gone, transport died mid-handshake. 255 is ssh's
own convention for "the connection itself failed", as distinct from any exit
code the remote command could have produced."""

_CHANNEL_EVENTS = (
    asyncssh.misc.TerminalSizeChanged,
    asyncssh.misc.BreakReceived,
    asyncssh.misc.SignalReceived,
    asyncssh.misc.SoftEOFReceived,
)
"""Out-of-band channel events asyncssh delivers by *raising* from a stream read
rather than returning data. None of them are meaningful for a pipe-bridged
session with no PTY, so each just ends the affected pump. Note these derive
from `Exception` directly, not from `asyncssh.Error`, so they need listing
separately from the genuine protocol failures."""


class _NoAuthServer(asyncssh.SSHServer):
    """No real authentication - see this feature's design spec for why: the
    socketpair this server listens on is only ever reachable by a local
    subprocess spawn, which already requires local shell access (the actual
    security boundary). Requiring SSH-level auth on top would check a
    credential that adds no real security."""

    def begin_auth(self, username: str) -> bool:
        """Returning `False` tells asyncssh no authentication is required."""
        return False


async def _handle_process(
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
    """
    # Passed as distinct argv entries, so the command reaches the container's
    # `sh -c` exactly as the client wrote it - no shell runs on this side.
    shell_argv = ["sh"] if process.command is None else ["sh", "-c", process.command]
    proc = await asyncio.create_subprocess_exec(
        cli_binary,
        "exec",
        "-i",
        container_name,
        *shell_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    proc_stdin = proc.stdin
    proc_stdout = proc.stdout
    assert proc_stdin is not None
    assert proc_stdout is not None

    async def pump_client_to_process() -> None:
        # Every one of these means "this direction is over", not "something is
        # wrong with dvt": channel events (above), the container's shell having
        # exited (OSError/BrokenPipeError), or the client vanishing mid-session
        # (asyncssh.ConnectionLost and friends, all under asyncssh.Error). This
        # must not escape - it runs as a task the session teardown awaits, and
        # an exception here would skip reporting the session's exit status.
        with contextlib.suppress(*_CHANNEL_EVENTS, asyncssh.Error, OSError):
            while True:
                data = await process.stdin.read(_CHUNK)
                if not data:
                    break
                proc_stdin.write(data.encode() if isinstance(data, str) else data)
                await proc_stdin.drain()
        with contextlib.suppress(OSError):
            proc_stdin.close()

    async def pump_process_to_client() -> None:
        # Same reasoning in the other direction: once the channel is gone there
        # is nowhere to put the container's output, which ends this pump but
        # still leaves a real exit code for `proc.wait()` to report.
        with contextlib.suppress(*_CHANNEL_EVENTS, asyncssh.Error, OSError):
            while True:
                chunk = await proc_stdout.read(_CHUNK)
                if not chunk:
                    break
                process.stdout.write(chunk.decode(errors="replace"))

    # The client half is a background task rather than a `gather` partner: a
    # client is under no obligation to ever send EOF, so waiting on it would
    # hang every session whose shell exits on its own.
    client_pump = asyncio.create_task(pump_client_to_process())
    try:
        await pump_process_to_client()
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


def _pump_stdio_to_socket(sock: socket.socket) -> None:
    """Bridge this process's real stdin/stdout to the socketpair end asyncssh
    isn't using. Runs in dedicated threads since it's blocking I/O on real
    file descriptors, alongside the asyncio event loop driving the server on
    the other end of the pair. Blocks until the server side closes."""

    def stdin_to_sock() -> None:
        try:
            while True:
                data = sys.stdin.buffer.read(_CHUNK)
                if not data:
                    break
                sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # Server closed the socket first; nothing more to forward.

    def sock_to_stdout() -> None:
        try:
            while True:
                data = sock.recv(_CHUNK)
                if not data:
                    break
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        except OSError:
            pass

    reader = threading.Thread(target=stdin_to_sock, daemon=True)
    writer = threading.Thread(target=sock_to_stdout, daemon=True)
    reader.start()
    writer.start()
    writer.join()  # server-side close (EOF from the socket) ends the bridge


def run_stdio_server(cli_binary: str, container_name: str) -> int:
    """Run a real (if minimal) SSH server bound to this process's own
    stdin/stdout, bridging the one session it'll ever handle to
    `docker/podman exec -i <container_name> sh`. Returns the bridged
    process's exit code.

    This is deliberately the one fallible entry point here that returns a
    plain `int` rather than a `Result`: it exists to turn a session into a
    process exit code for `dvt ssh --stdio`.
    """
    # `process.exit()` sets the SSH channel's status for the client, not a
    # Python return value, so `_handle_process`'s own return travels back out
    # through this list. Nothing yields between `_handle_process` returning
    # and the append, so the value is always recorded before the connection
    # can finish closing.
    exit_codes: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        try:
            exit_codes.append(
                await _handle_process(process, cli_binary, container_name)
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
            exit_codes.append(_EXIT_SESSION_FAILED)
            # Best-effort: tell the client too, if the channel is still alive.
            with contextlib.suppress(Exception):
                process.exit(_EXIT_SESSION_FAILED)

    async def main(server_sock: socket.socket) -> None:
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        conn = await asyncssh.run_server(
            server_sock,
            server_factory=_NoAuthServer,
            server_host_keys=[host_key],
            process_factory=process_factory,
            # We accept every client unconditionally (see `_NoAuthServer`), so
            # GSSAPI is dead weight - and leaving it enabled makes asyncssh
            # resolve this host's FQDN at startup, which costs seconds of
            # connection latency on machines behind slow reverse DNS.
            gss_host=None,
        )
        await conn.wait_closed()

    server_sock, stdio_sock = socket.socketpair()
    # A plain daemon thread, not `asyncio.to_thread`: the latter runs on the
    # loop's shared executor, which `asyncio.run` joins on shutdown with a
    # 300s timeout of its own - so cancelling the future would not stop the
    # thread, and a bridge still blocked in `recv` would hang the process for
    # five minutes rather than the five seconds documented above.
    bridge = threading.Thread(
        target=_pump_stdio_to_socket, args=(stdio_sock,), daemon=True
    )
    bridge.start()
    try:
        asyncio.run(main(server_sock))
    finally:
        # Closing the server end is what gives the bridge its EOF, and asyncio
        # has normally already done it by now; joining outside the loop lets
        # the bridge flush its last output without blocking anything.
        bridge.join(_BRIDGE_DRAIN_TIMEOUT)
        for sock in (stdio_sock, server_sock):
            with contextlib.suppress(OSError):
                sock.close()

    # No session ever opened (client connected and hung up) - not an error.
    return exit_codes[-1] if exit_codes else 0

    return asyncio.run(main())
