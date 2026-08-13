"""Bridges a PtyProcess (devtemplate.pty.spawn) to an asyncssh
SSHServerProcess - the pty-requesting-session counterpart to
ssh_server.py's plain-pipe _handle_process, used only when the SSH client
actually asked for a pty (see ssh_server.py for the branch)."""

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


def _pump_pty_to_socket(pty_proc: PtyProcess, sock: socket.socket) -> None:
    """Blocking: reads pty_proc in a loop, forwarding each chunk onto sock.
    Runs in a dedicated thread since PtyProcess.read() is a blocking OS/
    ConPTY call. Ends when pty_proc.read() returns "" (process exited)."""
    try:
        while True:
            chunk = pty_proc.read(_CHUNK)
            if not chunk:
                break
            sock.sendall(chunk.encode())
        sock.shutdown(socket.SHUT_WR)
    except OSError:
        pass  # peer closed first; nothing more to forward


def _pump_socket_to_pty(sock: socket.socket, pty_proc: PtyProcess) -> None:
    """Blocking: reads sock in a loop, forwarding each chunk to
    pty_proc.write(). The other half of the same thread pair as
    _pump_pty_to_socket. Incremental decoder for the same multi-byte-UTF-8-
    across-a-read-boundary reason as everywhere else in this codebase that
    decodes a byte stream."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            data = sock.recv(_CHUNK)
            if not data:
                break
            text = decoder.decode(data)
            if text:
                pty_proc.write(text)
    except OSError:
        pass


async def bridge_to_ssh_process(
    pty_proc: PtyProcess, process: asyncssh.SSHServerProcess
) -> int:
    """Bridge one opened, pty-requesting SSH session to pty_proc until
    either side ends, then return pty_proc's exit code (and report it to
    the SSH client via process.exit(), same contract ssh_server.py's own
    _handle_process already has for its non-pty sessions)."""
    loop = asyncio.get_running_loop()
    pty_sock, bridge_sock = socket.socketpair()
    bridge_sock.setblocking(False)

    reader = threading.Thread(
        target=_pump_pty_to_socket, args=(pty_proc, pty_sock), daemon=True
    )
    writer = threading.Thread(
        target=_pump_socket_to_pty, args=(pty_sock, pty_proc), daemon=True
    )
    reader.start()
    writer.start()

    async def pump_client_to_pty() -> None:
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                try:
                    data = await process.stdin.read(_CHUNK)
                except asyncssh.misc.TerminalSizeChanged as exc:
                    pty_proc.resize(exc.height, exc.width)
                    continue
                except _CHANNEL_EVENTS:
                    continue
                if not data:
                    break
                await loop.sock_sendall(
                    bridge_sock, data.encode() if isinstance(data, str) else data
                )
        with contextlib.suppress(OSError):
            bridge_sock.shutdown(socket.SHUT_WR)

    async def pump_pty_to_client() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                chunk = await loop.sock_recv(bridge_sock, _CHUNK)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    process.stdout.write(text)

    client_pump = asyncio.create_task(pump_client_to_pty())
    try:
        await pump_pty_to_client()
        exit_code = await loop.run_in_executor(None, pty_proc.wait)
    finally:
        client_pump.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await client_pump
        reader.join(_DRAIN_TIMEOUT)
        writer.join(_DRAIN_TIMEOUT)
        for sock in (pty_sock, bridge_sock):
            with contextlib.suppress(OSError):
                sock.close()
        pty_proc.close()

    process.exit(exit_code)
    return exit_code
