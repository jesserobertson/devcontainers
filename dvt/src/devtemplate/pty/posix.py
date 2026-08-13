"""POSIX pty backend: Linux/macOS, stdlib only. This module's own `import
pty` refers to the *stdlib* pty module - unambiguous under Python 3's
absolute-import resolution despite this package also being named `pty`, but
worth a comment so a future reader isn't confused seeing both."""

from __future__ import annotations

import codecs
import os
import struct

# POSIX-only imports, guarded so this module can be imported on Windows
# (for doctest discovery by pytest) even though it won't actually be used.
# The class and function bodies below only resolve these names at call time
# (inside method bodies), not at class-definition time, so they can be
# defined unconditionally; and spawn_pty_process()'s dispatcher in spawn.py
# guarantees posix.spawn() is never called on Windows in production anyway.
#
# The `# type: ignore[attr-defined]` comments further down are the type-check
# mirror of this: typeshed guards fcntl/termios/pty's contents and os's
# W*() helpers behind `sys.platform != "win32"`, so mypy run on a Windows
# host (as CI's windows-latest runner does) sees the modules but none of
# their members. The ignores are unused on Linux/macOS, hence this module's
# `warn_unused_ignores = false` override in pyproject.toml - deliberately
# scoped to that one warning here rather than disabling attr-defined for the
# whole file, so a genuine typo in any other attribute is still caught.
if os.name == "posix":
    import fcntl
    import pty  # stdlib pty module, not devtemplate.pty - see module docstring
    import termios


class PosixPtyProcess:
    def __init__(self, pid: int, master_fd: int) -> None:
        self._pid = pid
        self._master_fd = master_fd
        # Incremental so a multi-byte UTF-8 character split across two
        # os.read() calls decodes correctly instead of becoming replacement
        # characters at the split - same reasoning as ssh_server.py's
        # existing pump_process_to_client.
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def read(self, size: int = 4096) -> str:
        try:
            chunk = os.read(self._master_fd, size)
        except OSError:
            # EIO once the child has exited and closed its end of the pty -
            # a known pty quirk, not a genuine error. Treated as clean EOF,
            # matching this Protocol's contract.
            return ""
        if not chunk:
            return ""
        return self._decoder.decode(chunk)

    def write(self, data: str) -> None:
        encoded = data.encode()
        # os.write() may write fewer bytes than given; loop until it's all
        # sent, matching the same pattern ssh_server.py's stdio bridge uses.
        while encoded:
            encoded = encoded[os.write(self._master_fd, encoded) :]

    def resize(self, rows: int, cols: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)  # type: ignore[attr-defined]

    def wait(self) -> int:
        _, status = os.waitpid(self._pid, 0)
        # The int() calls keep this returning a concrete int under
        # warn_return_any: on a Windows-hosted mypy run the os.W*() helpers
        # are unknown attributes, so their results would otherwise be Any.
        if os.WIFEXITED(status):  # type: ignore[attr-defined]
            return int(os.WEXITSTATUS(status))  # type: ignore[attr-defined]
        if os.WIFSIGNALED(status):  # type: ignore[attr-defined]
            return 128 + int(os.WTERMSIG(status))  # type: ignore[attr-defined]
        return status

    def close(self) -> None:
        try:
            os.close(self._master_fd)
        except OSError:
            pass


def spawn(argv: list[str], rows: int, cols: int) -> PosixPtyProcess:
    """pty.fork() (not hand-rolled os.openpty() + subprocess.Popen) because
    it already correctly makes the pty slave the child's controlling
    terminal - simply inheriting an already-open fd via Popen(stdin=slave_fd)
    does not reliably confer that, and getting it right by hand needs an
    explicit setsid() plus a pathname re-open or ioctl(TIOCSCTTY)."""
    pid, master_fd = pty.fork()  # type: ignore[attr-defined]
    if pid == 0:
        # In the child: os.execvp never returns on success. The try/finally
        # guarantees this function - and by extension this whole process,
        # which is a full fork() of the running SSH server - can never fall
        # through to `return` below if exec fails for any reason (missing
        # binary, etc.). Without this, a failed exec would leave a second,
        # duplicate copy of the entire server running.
        try:
            os.execvp(argv[0], argv)
        finally:
            os._exit(127)
    proc = PosixPtyProcess(pid, master_fd)
    proc.resize(rows, cols)
    return proc
