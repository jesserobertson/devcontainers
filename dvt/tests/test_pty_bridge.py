from __future__ import annotations

import asyncio
import socket
import sys
import threading

import asyncssh
import pytest
from logerr import Err, Ok

from devtemplate.net import bounded_socketpair
from devtemplate.pty.bridge import bridge_to_ssh_process, pump_socket_to_pty
from devtemplate.pty.spawn import spawn_pty_process


class _NoAuthServer(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return False


async def _serve(sock, host_key, process_factory) -> asyncssh.SSHServerConnection:
    return await asyncssh.run_server(
        sock,
        server_factory=_NoAuthServer,
        server_host_keys=[host_key],
        process_factory=process_factory,
        gss_host=None,
        # asyncssh defaults line_editor=True: whenever a pty is requested
        # (term_type is set below) and the channel's encoding isn't None, it
        # runs its OWN server-side line-editing/local-echo emulation on
        # process.stdin before bridge_to_ssh_process ever sees the bytes -
        # a convenience feature for scripted clients that never allocated a
        # real terminal themselves (exactly what conn.create_process()
        # below is, since it writes to process.stdin directly rather than
        # driving a real terminal emulator). Confirmed by direct probing:
        # with the default on, client-sent "hello\r\n" arrived at pty_proc
        # as "hello\n\n" (the editor treats \r as End-Of-Line and
        # substitutes its own \n), which starved _ECHO_ONE_LINE's
        # readline() of the trailing newline it needed and hung the test.
        # A real pty bridge must forward genuinely raw bytes - the
        # container's own pty/shell (readline, bash, vim...) is what's
        # supposed to interpret them, exactly like a real sshd hands a real
        # terminal's raw bytes straight through - so this is disabled to
        # make process.stdin a faithful raw byte stream, matching what
        # bridge.py's own docstring already assumes real production traffic
        # looks like (see bridge_to_ssh_process's callers).
        line_editor=False,
    )


_ECHO_ONE_LINE = (
    "import sys\n"
    "line = sys.stdin.readline()\n"
    "sys.stdout.write('echo:' + line)\n"
    "sys.stdout.flush()\n"
)

_REPORT_SIZE_TWICE = (
    "import os, sys\n"
    "size = os.get_terminal_size()\n"
    "sys.stdout.write(f'{size.columns}x{size.lines}\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.readline()\n"
    "size = os.get_terminal_size()\n"
    "sys.stdout.write(f'{size.columns}x{size.lines}\\n')\n"
    "sys.stdout.flush()\n"
)

_EXIT_WITH_CODE = "import sys; sys.exit(9)"


async def _read_until(stream, marker: str, timeout: float = 10.0) -> str:
    """Read lines from an asyncssh stream until marker appears as a
    substring anywhere in the accumulated buffer, bounded by timeout.

    ConPTY prepends VT negotiation escape sequences (win32-input-mode/
    focus-tracking setup) ahead of the child's own output on every session -
    the same finding as devtemplate.pty.windows's own tests
    (tests/test_pty_spawn.py's _read_until) - so an exact first-line match
    against a pty-backed session's output is unreliable on Windows even
    through this async bridge. Unlike pywinpty's plain blocking read()
    (which needed a whole thread+join to bound, see test_pty_spawn.py's
    docstring), stream.readline() here is genuinely cancellable asyncio I/O,
    so a plain asyncio.wait_for is enough to bound each read.
    """
    buf = ""
    while marker not in buf:
        line = await asyncio.wait_for(stream.readline(), timeout)
        if not line:
            raise AssertionError(
                f"stream ended before {marker!r} appeared; got {buf!r}"
            )
        buf += line
    return buf


@pytest.mark.slow  # real spawn_pty_process() + a real asyncssh handshake, ~8s
@pytest.mark.asyncio
async def test_bridge_to_ssh_process_round_trips_data_both_directions():
    """Real client, real server, a real spawn_pty_process()-backed process -
    proves the full asyncssh <-> pty bridge, not just its two halves in
    isolation."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = bounded_socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        pty_proc = spawn_pty_process(
            [sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80
        )
        await bridge_to_ssh_process(pty_proc, process)

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            # Windows console (ConPTY) cooked-mode line input requires \r to
            # complete a line - a bare \n never terminates the child's
            # readline(), same finding as Task 2's windows.py tests. Unlike a
            # real terminal client (which sends \r for Enter and lets the
            # remote pty driver translate as needed), asyncssh's
            # process.stdin.write() here sends raw bytes verbatim with no
            # translation, and this bridge forwards them unmodified - so the
            # test itself must send \r\n to stand in for a real terminal's
            # Enter key. See bridge.py's own docstring: production traffic
            # always originates from a real SSH client's terminal emulator,
            # which already sends \r.
            process.stdin.write("hello\r\n")
            result = await process.wait()

    assert "echo:hello" in result.stdout
    assert result.exit_status == 0
    (await server_task).close()


@pytest.mark.slow  # real spawn_pty_process() + a real asyncssh handshake, ~8s
@pytest.mark.asyncio
async def test_bridge_to_ssh_process_forwards_client_resize_to_the_pty():
    """A client-side window-change request must reach the real pty, not just
    be swallowed - the same class of bug devtemplate.sshd.session's own
    TerminalSizeChanged test guards against for the non-pty path."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = bounded_socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        pty_proc = spawn_pty_process(
            [sys.executable, "-c", _REPORT_SIZE_TWICE], rows=24, cols=80
        )
        await bridge_to_ssh_process(pty_proc, process)

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process(term_type="xterm", term_size=(80, 24))
            # Substring, not exact-match: see _read_until's docstring for why
            # (ConPTY's VT preamble lands in this same first line).
            assert "80x24" in await _read_until(process.stdout, "80x24")

            process.change_terminal_size(120, 50)
            await asyncio.sleep(0.2)

            # See the \r\n note in the round-trip test above - same reason.
            process.stdin.write("go\r\n")
            assert "120x50" in await _read_until(process.stdout, "120x50")

            process.stdin.write_eof()
            await process.wait()

    (await server_task).close()


@pytest.mark.slow  # real spawn_pty_process() + a real asyncssh handshake, ~8s
@pytest.mark.asyncio
async def test_bridge_to_ssh_process_ignores_break_and_signal_requests():
    """Regression guard matching today's behaviour (see the design spec's
    Testing section): a client-sent break or signal request must be silently
    ignored, exactly like devtemplate.sshd.session's own non-pty path already treats
    them - *not* forwarded to the pty (there is no SSH-protocol-level
    concept for either on a real terminal; a client typing Ctrl-C instead
    arrives as an ordinary 0x03 byte, covered by the round-trip test above)
    and, critically, not allowed to end the session. Proven by sending both,
    then confirming the bridge is still alive and forwarding ordinary data
    afterwards."""
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = bounded_socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        pty_proc = spawn_pty_process(
            [sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80
        )
        await bridge_to_ssh_process(pty_proc, process)

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")

            process.send_break(1000)
            process.send_signal("INT")
            await asyncio.sleep(0.2)

            # Session must still be alive and forwarding data normally after
            # the break/signal - see the \r\n note in the round-trip test
            # above for why this needs \r\n rather than a bare \n.
            process.stdin.write("hello\r\n")
            result = await process.wait()

    assert "echo:hello" in result.stdout
    assert result.exit_status == 0
    (await server_task).close()


@pytest.mark.slow  # real spawn_pty_process() + a real asyncssh handshake, ~8s
@pytest.mark.asyncio
async def test_bridge_to_ssh_process_returns_the_pty_processs_exit_code():
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = bounded_socketpair()
    returned: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        pty_proc = spawn_pty_process(
            [sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80
        )
        returned.append(await bridge_to_ssh_process(pty_proc, process))

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process(term_type="xterm")
            result = await process.wait()

    assert result.exit_status == 9
    assert returned == [9]
    (await server_task).close()


class _FakePtyProcess:
    """A minimal PtyProcess double whose write() fails on demand - lets
    pump_socket_to_pty's handling of a real backend's Err(OSError) (see
    PosixPtyProcess.write/WindowsPtyProcess.write) be exercised directly,
    without needing a real pty or a full bridge_to_ssh_process session."""

    def __init__(self, *, fail_after: int = 0) -> None:
        self.calls: list[str] = []
        self._fail_after = fail_after

    def write(self, data: str):
        self.calls.append(data)
        if len(self.calls) > self._fail_after:
            return Err(OSError("pty is closed"))
        return Ok(None)


def test_pump_socket_to_pty_stops_cleanly_when_write_returns_err():
    # Reproduces the actual bug this Result-returning contract exists to
    # fix: a write arriving after the pty is gone must make the pump
    # thread stop, not crash it with an unhandled exception (the original
    # report: an EOFError traceback on exiting a container shell). Doesn't
    # assert on fake_proc.calls' exact contents - the underlying socket is
    # a TCP loopback pair (see bounded_socketpair), which doesn't preserve
    # message boundaries, so a single sendall() may arrive as one or more
    # recv()s; only "at least one write was attempted, then the thread
    # stopped" is a safe thing to assert.
    server_sock, client_sock = bounded_socketpair()
    fake_proc = _FakePtyProcess(fail_after=0)

    thread = threading.Thread(
        target=pump_socket_to_pty, args=(server_sock, fake_proc), daemon=True
    )
    thread.start()
    try:
        client_sock.sendall(b"late data")
        thread.join(timeout=5)
        assert not thread.is_alive(), (
            "pump_socket_to_pty did not stop after write() returned Err"
        )
        assert fake_proc.calls, "write() was never even attempted"
    finally:
        client_sock.close()
        server_sock.close()


def test_pump_socket_to_pty_keeps_pumping_while_write_returns_ok():
    server_sock, client_sock = bounded_socketpair()
    fake_proc = _FakePtyProcess(fail_after=1_000_000)  # never fails

    thread = threading.Thread(
        target=pump_socket_to_pty, args=(server_sock, fake_proc), daemon=True
    )
    thread.start()
    try:
        client_sock.sendall(b"hello")
        # EOF (not a write() failure) is the only thing that should end
        # the loop here - shutting down the write side is what pump_pty_to_socket's
        # own counterpart relies on in the real bridge, see bridge_to_ssh_process.
        client_sock.shutdown(socket.SHUT_WR)
        thread.join(timeout=5)
        assert not thread.is_alive()
        # Joined because a single small sendall() may still arrive as more
        # than one recv() on this backend - see the Err-path test above.
        assert "".join(fake_proc.calls) == "hello"
    finally:
        client_sock.close()
        server_sock.close()
