"""Windows pty backend: wraps pywinpty's PtyProcess, which already handles
CreatePseudoConsole, process creation, and resize correctly - the same
approach VS Code's own integrated terminal and node-pty use, rather than
hand-writing ctypes against the Win32 ConPTY API for a narrow use case."""

from __future__ import annotations

import sys

# Windows-only import, guarded so this module can be imported on Linux/macOS
# (for doctest discovery by pytest) even though it won't actually be used
# there - pywinpty is declared as a win32-only dependency in pyproject.toml,
# so it's simply not installed on those platforms. The class and function
# bodies below only resolve _WinPtyProcess at call time (inside spawn()'s
# body), not at class/function-definition time, so they can be defined
# unconditionally; and spawn_pty_process()'s dispatcher in spawn.py
# guarantees windows.spawn() is never called on non-Windows platforms.
if sys.platform == "win32":
    from winpty import PtyProcess as _WinPtyProcess


class WindowsPtyProcess:
    def __init__(self, inner: _WinPtyProcess) -> None:
        self._inner = inner

    def read(self, size: int = 4096) -> str:
        try:
            return self._inner.read(size)
        except EOFError:
            # pywinpty's own documented contract: read() raises EOFError
            # once the child has exited and output is exhausted, rather
            # than returning "". Normalized here to match this package's
            # PtyProcess.read() contract (return "" on EOF), so bridge.py
            # doesn't need to know which backend it's talking to.
            return ""

    def write(self, data: str) -> None:
        self._inner.write(data)

    def resize(self, rows: int, cols: int) -> None:
        self._inner.setwinsize(rows, cols)

    def wait(self) -> int:
        return self._inner.wait()

    def close(self) -> None:
        self._inner.close()


def spawn(argv: list[str], rows: int, cols: int) -> WindowsPtyProcess:
    inner = _WinPtyProcess.spawn(argv, dimensions=(rows, cols))
    return WindowsPtyProcess(inner)
