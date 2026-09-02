"""Bridges this process's real stdin/stdout to the socketpair end asyncssh
isn't using - the transport-level half of the dvt ssh --stdio bridge, kept
separate from session.py's session-level handling."""

from __future__ import annotations

import os
import socket
import sys
import threading

from devtemplate.pty.constants import CHUNK

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
