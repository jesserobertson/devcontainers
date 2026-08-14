"""Spawns a process attached to a real OS pseudo-terminal, dispatching to a
platform-specific backend. Used only by devtemplate.pty.bridge, for SSH
sessions that requested a pty - see ssh_server.py's _handle_process."""

from __future__ import annotations

import sys
from typing import Protocol

__all__ = ["PtyProcess", "spawn_pty_process"]


class PtyProcess(Protocol):
    """A process attached to a real OS pseudo-terminal. Backends:
    devtemplate.pty.posix (stdlib pty.fork(), Linux/macOS) or
    devtemplate.pty.windows (pywinpty/ConPTY, Windows) - chosen by
    spawn_pty_process() based on sys.platform. The two backends share no
    implementation, only this structural interface."""

    def read(self, size: int = 4096) -> str:
        """Block until at least one character is available or the process
        has exited, then return up to size characters. Returns "" once the
        process has exited and all its output has been drained - never
        raises on ordinary end-of-output."""
        ...

    def write(self, data: str) -> None:
        """Write data to the process's pty. Blocks until fully sent."""
        ...

    def resize(self, rows: int, cols: int) -> None:
        """Update the pty's terminal size, delivering SIGWINCH (POSIX) or
        the console-resize equivalent (Windows) to the running process."""
        ...

    def wait(self) -> int:
        """Block until the process exits; return its exit code."""
        ...

    def close(self) -> None:
        """Release the pty's OS resources. Safe to call after the process
        has already exited."""
        ...


def spawn_pty_process(argv: list[str], rows: int, cols: int) -> PtyProcess:
    """Spawn argv attached to a real OS pseudo-terminal sized rows x cols.

    Dispatches by sys.platform - devtemplate.pty.posix (stdlib pty.fork())
    on Linux/macOS, devtemplate.pty.windows (pywinpty/ConPTY) on win32. The
    Windows import is deferred to call time so this module - and everything
    that imports it - loads fine on platforms without pywinpty installed.
    """
    if sys.platform == "win32":
        from devtemplate.pty.windows import spawn

        return spawn(argv, rows, cols)
    from devtemplate.pty.posix import spawn

    return spawn(argv, rows, cols)
