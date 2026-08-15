from __future__ import annotations

import sys
import threading
import time

import pytest

from devtemplate.pty.spawn import spawn_pty_process

# Every test here spawns a real OS process attached to a real pty
# (pty.fork() on POSIX, pywinpty/ConPTY on Windows) via spawn_pty_process -
# genuine process-creation + terminal-allocation overhead, not something a
# unit test's "fast, fully mocked" definition (see conftest.py's marker
# registration) fits: ~3s each on this Windows dev machine, dominating the
# "unit"/"fast" tiers' total runtime out of proportion to what they add.
pytestmark = pytest.mark.slow

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
    "sys.stdout.write('isatty=yes\\n' if sys.stdin.isatty() else 'isatty=no\\n')\n"
    "sys.stdout.write('CHECKED\\n')\n"
    "sys.stdout.flush()\n"
)
"""Deliberately prints two non-overlapping answers plus an unconditional
CHECKED sentinel. An earlier version printed TTY/NOTTY and the tests below
asserted `"TTY" in output` - which is also true of "NOTTY", so the one test
proving this feature's core fix could never actually fail. The sentinel
means the read completes on either branch, so it's the assertions (not a
read timeout) that decide the result."""

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


def _read_until(proc, marker: str, timeout: float = 10.0) -> str:
    """Read from proc until marker appears anywhere in the accumulated
    output, returning everything read so far; raise AssertionError if it
    never does.

    A substring search over the whole buffer, not an exact line match,
    because neither backend hands back the child's output verbatim:

    - POSIX: pty.fork() leaves the slave pty in full default canonical mode
      (ECHO, ICANON, OPOST, ONLCR, ICRNL all on). ONLCR turns every '\\n'
      the child writes into '\\r\\n' on the master, so even pure output
      arrives with a trailing '\\r'; ECHO injects whatever this test
      write()s back into the read stream ahead of the child's real reply.
    - Windows: ConPTY prepends VT negotiation escape sequences
      (win32-input-mode / focus-tracking setup) to every session's output
      before any child output, and likewise echoes written input.

    Bounded by a wall-clock deadline, and stopped early at EOF, so a
    genuine regression (the child never producing the expected output)
    fails fast with a clear assertion instead of hanging the test run or
    spinning out the full deadline once the child has already gone.

    Each read runs on its own daemon thread, joined with a timeout,
    because a deadline merely checked between calls to proc.read() would
    never get a chance to fire if a single call never returns - execution
    never comes back around the loop to check it. pywinpty's
    PtyProcess.read() is a plain blocking socket recv() with no timeout of
    its own, and POSIX's os.read() on the pty master likewise blocks until
    the child writes something.

    Deliberately a bare threading.Thread(daemon=True), not
    concurrent.futures.ThreadPoolExecutor - a ThreadPoolExecutor registers
    an atexit hook (concurrent.futures.thread._python_exit) that
    unconditionally joins every worker thread it ever created, so an
    abandoned pool thread still stuck in recv() would hang pytest's own
    process exit even after this function had already raised. A daemon
    thread carries no such join-at-exit obligation: a read that times out
    leaves its thread abandoned, but that's a leaked thread only - an
    acceptable, known tradeoff for a test helper's failure path, and it
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

        def _do_read(result: dict = result) -> None:
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
        if not result["chunk"]:
            # "" is this Protocol's EOF: the child has exited and its output
            # is drained, so the marker is never going to arrive. Raise now
            # rather than re-reading "" until the deadline expires.
            raise AssertionError(
                f"child exited before producing {marker!r}; got {buf!r}"
            )
        buf += result["chunk"]
    return buf


def _shutdown(proc) -> None:
    """Tear the child down close-first, then reap.

    Order matters on the failure path: these tests interleave reads and
    writes, so an assertion that fails before its matching write() leaves
    the child blocked forever in readline(), and a plain proc.wait() would
    then block forever too - turning a clean test failure into a hung run
    (this repo configures no pytest-timeout, so CI would hang until
    GitHub's own job timeout). Closing first hangs the child's controlling
    terminal up (closing the pty master raises SIGHUP on POSIX; pywinpty's
    close() terminates the child on Windows), so the wait() below always
    has something to reap.

    Not used by the exit-code tests, which need wait()'s real status and
    whose children always exit on their own.
    """
    proc.close()
    proc.wait()


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
        buf = _read_until(proc, "CHECKED")
        assert "isatty=yes" in buf
        assert "isatty=no" not in buf
    finally:
        _shutdown(proc)


@_posix_only
def test_spawn_pty_process_write_reaches_the_child():
    # The pty slave's default ECHO also puts the written "hello" into the
    # read stream, ahead of the child's real reply - hence the substring
    # search for the reply's own "echo:" prefix rather than an exact
    # first-line match. See _read_until's docstring.
    proc = spawn_pty_process([sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80)
    try:
        assert proc.write("hello\n").is_ok()
        assert "echo:hello" in _read_until(proc, "echo:hello")
    finally:
        _shutdown(proc)


@_posix_only
def test_spawn_pty_process_write_after_close_returns_err():
    # os.write() on a pty master fd that's already been closed raises
    # OSError natively - devtemplate.pty.posix.write() wraps that as
    # Err(OSError(...)) rather than letting it propagate, matching
    # PtyProcess.write()'s Result-returning contract (see spawn.py) and
    # the equivalent Err WindowsPtyProcess.write() returns for the
    # identical condition on that backend.
    proc = spawn_pty_process([sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80)
    proc.wait()
    proc.close()
    result = proc.write("late data\n")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), OSError)


@_posix_only
def test_spawn_pty_process_resize_changes_reported_terminal_size():
    proc = spawn_pty_process(
        [sys.executable, "-c", _REPORT_SIZE_TWICE], rows=24, cols=80
    )
    try:
        assert "80x24" in _read_until(proc, "80x24")
        proc.resize(rows=50, cols=120)
        proc.write("go\n")
        assert "120x50" in _read_until(proc, "120x50")
    finally:
        _shutdown(proc)


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
    # searches the whole buffer rather than matching an exact first line -
    # see _read_until's docstring. The CHECKED sentinel arrives whichever
    # branch the child took, so the assertions below (not a read timeout)
    # are what decide this test.
    proc = spawn_pty_process([sys.executable, "-c", _CHECK_ISATTY], rows=24, cols=80)
    try:
        buf = _read_until(proc, "CHECKED")
        assert "isatty=yes" in buf
        assert "isatty=no" not in buf
    finally:
        _shutdown(proc)


@_windows_only
def test_spawn_pty_process_write_reaches_the_child_windows():
    # Windows console cooked-mode line input needs \r to complete a line -
    # a bare \n (which suffices on POSIX) never terminates the child's
    # readline(), so the write below uses \r\n. See _read_until's docstring
    # for why the read side also needs the substring-search helper (ConPTY
    # echoes the written input back into the output stream first).
    proc = spawn_pty_process([sys.executable, "-c", _ECHO_ONE_LINE], rows=24, cols=80)
    try:
        assert proc.write("hello\r\n").is_ok()
        assert "echo:hello" in _read_until(proc, "echo:hello")
    finally:
        _shutdown(proc)


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
        _shutdown(proc)


@_windows_only
def test_spawn_pty_process_wait_returns_the_exit_code_windows():
    proc = spawn_pty_process([sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80)
    try:
        assert proc.wait() == 42
    finally:
        proc.close()


@_windows_only
def test_spawn_pty_process_write_after_exit_returns_err_windows():
    # pywinpty's own write() raises EOFError once the pty has been torn
    # down (the child already exited) - devtemplate.pty.windows.write()
    # translates that into Err(OSError(...)) rather than letting it
    # propagate, matching PtyProcess.write()'s Result-returning contract
    # (see spawn.py) and the equivalent OSError PosixPtyProcess.write()
    # returns as Err for the identical condition. Reproduced for real: a
    # client's in-flight bytes arriving just after the shell exits used to
    # crash devtemplate.pty.bridge's pump_socket_to_pty thread with an
    # unhandled EOFError instead of being handled like every other
    # backend/condition.
    proc = spawn_pty_process([sys.executable, "-c", _EXIT_WITH_CODE], rows=24, cols=80)
    proc.wait()
    try:
        result = proc.write("late data\r\n")
        assert result.is_err()
        assert isinstance(result.unwrap_err(), OSError)
    finally:
        proc.close()
