"""Local-loopback socket plumbing shared by devtemplate.sshd and
devtemplate.pty, both of which bridge real OS I/O into asyncio via an
in-process `socket.socketpair()`."""

from __future__ import annotations

import queue
import socket
import threading

__all__ = ["bounded_socketpair"]

TIMEOUT = 3.0
"""Generous bound for a purely local, in-process socket pairing: a healthy
attempt (POSIX's real socketpair(2) syscall, or Windows' loopback-TCP
emulation) completes in well under a second."""

ATTEMPTS = 3
"""Retries use a fresh ephemeral port each time, since a single stalled
attempt gives no reason to expect the same port to behave differently."""


def bounded_socketpair(
    *, timeout: float = TIMEOUT, attempts: int = ATTEMPTS
) -> tuple[socket.socket, socket.socket]:
    """`socket.socketpair()`, bounded against an indefinite hang.

    On POSIX this is a real `socketpair(2)` kernel syscall - purely
    in-process, unable to block on network I/O. On Windows, the stdlib has
    no such syscall, and `socket.socketpair()` falls back to a pure-Python
    emulation that binds a real loopback TCP listener and connects to it -
    genuine network-stack I/O, with no timeout of its own. Observed directly
    (2026-08-15, via a live-hung `pytest` process caught mid-run): under
    real system load, that loopback connect can silently never complete,
    leaving the fallback's blocking `accept()` waiting forever - most likely
    local security/network-filtering software intercepting the handshake.

    Each attempt runs on a daemon thread rather than `concurrent.futures`,
    so a stalled attempt that we give up on cannot block process shutdown
    (the abandoned thread, and the socketpair() call still running on it,
    are simply killed with the process rather than joined) - the same
    tradeoff `devtemplate.pty.bridge`'s own pump threads already make.
    """
    last_error: BaseException = TimeoutError("attempts must be >= 1")
    for _ in range(attempts):
        outcome: queue.Queue[tuple[socket.socket, socket.socket] | OSError] = (
            queue.Queue(maxsize=1)
        )

        def attempt(outcome: queue.Queue) -> None:
            try:
                outcome.put(socket.socketpair())
            except OSError as exc:
                outcome.put(exc)

        thread = threading.Thread(target=attempt, args=(outcome,), daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            last_error = TimeoutError(
                f"socket.socketpair() did not complete within {timeout}s"
            )
            continue
        result = outcome.get()
        if isinstance(result, OSError):
            last_error = result
            continue
        return result

    raise TimeoutError(
        f"socket.socketpair() did not succeed after {attempts} attempts "
        f"of {timeout}s each"
    ) from last_error
