"""Windows pty backend: wraps pywinpty's PtyProcess, which already handles
CreatePseudoConsole, process creation, and resize correctly - the same
approach VS Code's own integrated terminal and node-pty use, rather than
hand-writing ctypes against the Win32 ConPTY API for a narrow use case."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

# Windows-only import, guarded so this module can be imported on Linux/macOS
# (for doctest discovery by pytest) even though it won't actually be used
# there - pywinpty is declared as a win32-only dependency in pyproject.toml,
# so it's simply not installed on those platforms. The class and function
# bodies below only resolve _WinPtyProcess at call time (inside spawn()'s
# body), not at class/function-definition time, so they can be defined
# unconditionally; and spawn_pty_process()'s dispatcher in spawn.py
# guarantees windows.spawn() is never called on non-Windows platforms.
#
# The `TYPE_CHECKING or` is what makes this analysable off Windows: mypy
# defaults --platform to the host, so on the Linux/macOS CI runners it would
# otherwise treat the sys.platform test as statically false, skip the import
# entirely, and then reject the `inner: _WinPtyProcess` annotation below as
# an undefined name. TYPE_CHECKING is always False at runtime, so the real
# sys.platform test still governs what actually gets imported.
if TYPE_CHECKING or sys.platform == "win32":
    from winpty import PtyProcess as _WinPtyProcess

__all__ = ["WindowsPtyProcess", "spawn"]


class WindowsPtyProcess:
    def __init__(self, inner: _WinPtyProcess) -> None:
        self._inner = inner

    def read(self, size: int = 4096) -> str:
        try:
            # pywinpty ships no type information, so its return values are
            # Any; the str()/int() calls here are what let this module honour
            # the PtyProcess Protocol's concrete types under warn_return_any.
            return str(self._inner.read(size))
        except EOFError:
            # pywinpty's own documented contract: read() raises EOFError
            # once the child has exited and output is exhausted, rather
            # than returning "". Normalized here to match this package's
            # PtyProcess.read() contract (return "" on EOF), so bridge.py
            # doesn't need to know which backend it's talking to.
            return ""

    def write(self, data: str) -> None:
        try:
            self._inner.write(data)
        except EOFError as exc:
            # pywinpty's own documented contract: write() raises EOFError
            # once the pty has been torn down (the child already exited),
            # mirroring read()'s own EOFError-on-EOF behavior above.
            # Re-raised as OSError so callers (bridge.py's
            # pump_socket_to_pty) can treat "the pty is gone" uniformly
            # across backends without needing backend-specific exception
            # handling - POSIX's os.write() already raises OSError for the
            # identical condition natively.
            raise OSError("Pty is closed") from exc

    def resize(self, rows: int, cols: int) -> None:
        self._inner.setwinsize(rows, cols)

    def wait(self) -> int:
        return int(self._inner.wait())

    def close(self) -> None:
        self._inner.close()


def spawn(argv: list[str], rows: int, cols: int) -> WindowsPtyProcess:
    inner = _WinPtyProcess.spawn(argv, dimensions=(rows, cols))
    return WindowsPtyProcess(inner)
