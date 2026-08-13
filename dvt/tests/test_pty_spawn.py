from __future__ import annotations

import sys
import threading
import time

import pytest

from devtemplate.pty.spawn import spawn_pty_process

_posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="exercises the stdlib pty.fork() backend, POSIX only",
)

_windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="exercises the pywinpty/ConPTY backend, Windows only",
)

_CHECK_ISATTY = (
    "import sys\n"
    "sys.stdout.write('TTY\\n' if sys.stdin.isatty() else 'NOTTY\\n')\n"
    "sys.stdout.flush()\n"
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

_EXIT_WITH_CODE = "import sys; sys.exit(42)"


def _read_line(proc) -> str:
    buf = ""
    while "\n" not in buf:
        buf += proc.read()
    return buf.split("\n", 1)[0]


def _read_until(proc, marker: str, timeout: float = 10.0) -> str:
    """Windows-only helper: ConPTY prepends VT negotiation escape sequences
    (win32-input-mode / focus-tracking setup) to every session's output
    before any child output, and also echoes written input back into the
    stream - neither of which the shared _read_line helper (built for
    POSIX's plain, unadorned pty output) can assume away. Reads until marker
    appears as a substring anywhere in the accumulated buffer, bounded by a
    wall-clock deadline so a genuine regression (the child never producing
    the expected output) fails fast with a clear assertion instead of
    hanging the test run.

    pywinpty's PtyProcess.read() is a plain blocking socket recv() with no
    timeout of its own, so a deadline merely checked between calls to
    proc.read() would never get a chance to fire if a single call never
    returns - execution never comes back around the loop to check it. Each
    read therefore runs on its own daemon thread, joined with a timeout: if
    the child hasn't produced the marker within the deadline, this raises
    promptly instead of blocking forever.

    Deliberately a bare threading.Thread(daemon=True), not
    concurrent.futures.ThreadPoolExecutor - a ThreadPoolExecutor registers
    an atexit hook (concurrent.futures.thread._python_exit) that
    unconditionally joins every worker thread it ever created, so an
    abandoned pool thread still stuck in recv() would hang pytest's own
    process exit even after this function had already raised. A daemon
    thread carries no such join-at-exit obligation: a read that times out
    leaves its thread abandoned, stuck in recv() forever since pywinpty's
    read() offers no way to interrupt it, but that's a leaked thread only -
    an acceptable, known tradeoff for a test helper's failure path, and it
    does not block this function's return or the test process's exit.
    """
    deadline = time.monotonic() + timeout
    buf = ""
    while marker not in buf:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"timed out after {timeout}s waiting for {marker!r} in "
                f"output; got {buf!r}"
            )
        result: dict = {}

        def _do_read() -> None:
            try:
                result["chunk"] = proc.read()
            except Exception as exc:  # pragma: no cover - defensive
                result["error"] = exc

        thread = threading.Thread(target=_do_read, daemon=True)
        thread.start()
        thread.join(timeout=remaining)
        if thread.is_alive():
            raise AssertionError(
                f"timed out after {timeout}s waiting for {marker!r} in "
                f"output; got {buf!r}"
            )
        if "error" in result:
            raise result["error"]
        buf += result["chunk"]
    return buf


@_posix_only
def test_spawn_pty_process_gives_the_child_a_real_tty():
    """The core bug this feature exists to fix: a process spawned through
    plain pipes (today's docker/podman exec -i path) sees isatty() as False,
    which is exactly why fish and similar shells skip printing a prompt.
    A process spawned via spawn_pty_process must see a real tty instead -
    the same OS-level check docker's own CLI performs on its inherited
    stdin when given -t, so proving it here proves the primitive that
    satisfies docker's requirement too."""
    proc = spawn_pty_process([sys.executable, "-c", _CHECK_ISATTY], rows=24, cols=80)
    try:
        assert _read_line(proc) == "TTY"
    finally:
        proc.wait()
        proc.close()


@_posix_only
def test_spawn_pty_process_write_reaches_the_child():
    proc = spawn_pty_process([sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80)
    try:
        proc.write("hello\n")
        assert _read_line(proc) == "echo:hello"
    finally:
        proc.wait()
        proc.close()


@_posix_only
def test_spawn_pty_process_resize_changes_reported_terminal_size():
    proc = spawn_pty_process(
        [sys.executable, "-c", _REPORT_SIZE_TWICE], rows=24, cols=80
    )
    try:
        assert _read_line(proc) == "80x24"
        proc.resize(rows=50, cols=120)
        proc.write("go\n")
        assert _read_line(proc) == "120x50"
    finally:
        proc.wait()
        proc.close()


@_posix_only
def test_spawn_pty_process_wait_returns_the_exit_code():
    proc = spawn_pty_process([sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80)
    try:
        assert proc.wait() == 42
    finally:
        proc.close()


@_windows_only
def test_spawn_pty_process_gives_the_child_a_real_tty_windows():
    # ConPTY prepends VT negotiation escape sequences (and a \r, not just
    # \n, terminates the line) before the child's own output, so this
    # checks "TTY" appears rather than an exact first-line match - see
    # _read_until's docstring.
    proc = spawn_pty_process([sys.executable, "-c", _CHECK_ISATTY], rows=24, cols=80)
    try:
        assert "TTY" in _read_until(proc, "TTY")
    finally:
        proc.wait()
        proc.close()


@_windows_only
def test_spawn_pty_process_write_reaches_the_child_windows():
    # Windows console cooked-mode line input needs \r to complete a line -
    # a bare \n (which suffices on POSIX) never terminates the child's
    # readline(), so the write below uses \r\n. See _read_until's docstring
    # for why the read side also needs the substring-search helper (ConPTY
    # echoes the written input back into the output stream first).
    proc = spawn_pty_process([sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80)
    try:
        proc.write("hello\r\n")
        assert "echo:hello" in _read_until(proc, "echo:hello")
    finally:
        proc.wait()
        proc.close()


@_windows_only
def test_spawn_pty_process_resize_changes_reported_terminal_size_windows():
    proc = spawn_pty_process(
        [sys.executable, "-c", _REPORT_SIZE_TWICE], rows=24, cols=80
    )
    try:
        assert "80x24" in _read_until(proc, "80x24")
        proc.resize(rows=50, cols=120)
        proc.write("go\r\n")
        assert "120x50" in _read_until(proc, "120x50")
    finally:
        proc.wait()
        proc.close()


@_windows_only
def test_spawn_pty_process_wait_returns_the_exit_code_windows():
    proc = spawn_pty_process([sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80)
    try:
        assert proc.wait() == 42
    finally:
        proc.close()
