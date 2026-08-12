# SSH PTY support for `dvt ssh --stdio`

## Problem

`dvt ssh --stdio <name>` (the `ProxyCommand` target a real `ssh <name>` uses) bridges each
session's SSH channel to `docker`/`podman exec -i <container> sh` — `-i` only, never `-t` — so the
container-side shell has no real tty. This isn't merely "no Ctrl-C" (the current wording in
`docs/content/commands.md`'s known-gaps section): shells like `fish` check `isatty(stdin)` and
silently skip printing their prompt/banner entirely without one, so an otherwise fully-functional
session looks hung — piping commands into it executes them and returns output correctly, but
there's no visual sign the session is alive. There's also no job control and no window-resize
handling for full-screen programs (`vim`, `top`).

`dvt ssh <name>` (no `ssh` client involved — `exec_interactive` in `ssh.py`, direct
`docker exec -it` inheriting this process's own real terminal) is unaffected; this is specific to
the SSH-bridged path.

## Goals

- A client that requests a pty over the `dvt ssh --stdio` bridge (an interactive `ssh <name>`, or
  an explicit `ssh -t <name> <command>`) gets a real prompt/banner, working Ctrl-C and job
  control, and correctly-sized full-screen programs — parity with `dvt ssh <name>`'s direct path.
- Works on all four platforms `dvt` ships on (`win-64`, `linux-64`, `osx-arm64`, `osx-64` per
  `pyproject.toml`), with genuine automated test coverage on each via the existing
  `ubuntu-latest`/`macos-latest`/`windows-latest` CI matrix (`dvt-ci.yml`) — not "POSIX now,
  Windows later," since this session's reported bug was hit on Windows.

## Non-goals

- No change to non-pty exec sessions (`ssh <name> "cmd"`, what VS Code Remote-SSH/JetBrains
  Gateway are expected to rely on) — these keep today's separate stdin/stdout/stderr pipes,
  completely untouched. See "stdout/stderr merging" below for why pty sessions and exec sessions
  necessarily behave differently here, and why that's not a regression.
- No SFTP subsystem (tracked as a separate, unrelated known gap).
- No fallback to today's pipe-based behavior if pty allocation itself fails (missing `pywinpty`,
  OS resource exhaustion, etc.) — the session fails loudly through the existing
  `process_factory` error path (exit 255, stderr diagnostic) rather than silently reproducing the
  "looks hung" bug this feature exists to fix.

## Architecture: a new `devtemplate.pty` package

Four new files, no changes to how any *other* existing module is organized:

- **`src/devtemplate/pty/__init__.py`** — empty, matching the existing `commands/__init__.py`
  precedent. Callers import submodule contents directly by dotted path
  (`from devtemplate.pty.spawn import spawn_pty_process`), the same style `cli.py` already uses
  for `devtemplate.commands.info`/`devtemplate.commands.init`.
- **`src/devtemplate/pty/spawn.py`** — the platform-dispatch entry point:

  ```python
  class PtyProcess(Protocol):
      def read(self, size: int) -> bytes: ...
      def write(self, data: bytes) -> None: ...
      def resize(self, rows: int, cols: int) -> None: ...
      def wait(self) -> int: ...
      def close(self) -> None: ...

  def spawn_pty_process(argv: list[str], rows: int, cols: int) -> PtyProcess:
      """Spawn argv attached to a real OS pseudo-terminal, sized rows x cols.
      Dispatches to devtemplate.pty.posix or devtemplate.pty.windows by
      sys.platform - the two backends have no interface in common beyond this
      Protocol, since the underlying OS primitives (POSIX pty vs Windows
      ConPTY) are unrelated."""
  ```

- **`src/devtemplate/pty/posix.py`** — Linux/macOS backend. Uses only the stdlib: `pty.fork()`
  (not hand-rolled `os.openpty()` + `subprocess.Popen`) because `pty.fork()` already correctly
  handles making the pty slave the child's controlling terminal — a detail that's easy to get
  subtly wrong by hand (simply inheriting an already-open fd via `Popen(stdin=slave_fd)` does not
  reliably confer controlling-terminal status; getting it right generally needs an explicit
  `setsid()` plus either a pathname re-open or `ioctl(TIOCSCTTY)` in the child, which is exactly
  what `pty.fork()` already does internally so this code doesn't have to). In the child branch
  (`pid == 0`), calls
  `os.execvp(argv[0], argv)` immediately — no intervening Python-level work, matching the same
  fork-then-exec safety profile CPython's own `subprocess` module already relies on in
  multi-threaded programs (unlike an arbitrary `preexec_fn` callback, which the stdlib
  documentation specifically warns is unsafe with threads). In the parent, wraps `master_fd` and
  `pid`; `.resize()` uses `fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ...)`; `.wait()` uses
  `os.waitpid`. A read from `master_fd` after the child has exited and closed its end of the pty
  raises `OSError` (`EIO`) rather than returning `b""` — a known pty quirk, treated as clean EOF,
  not a genuine error. (`posix.py` itself does `import pty` for the stdlib module of the same
  name as this package — Python 3's absolute-import resolution means this is unambiguous, but
  worth a one-line comment so a future reader isn't confused seeing both.)
  No new dependency.
- **`src/devtemplate/pty/windows.py`** — Windows backend, a thin wrapper around `pywinpty`'s
  `winpty.PtyProcess.spawn(argv, dimensions=(rows, cols))`, which already handles
  `CreatePseudoConsole`, process creation, and resize (`.setwinsize()`) correctly — the same
  approach VS Code's own integrated terminal and `node-pty` use, rather than hand-writing ctypes
  against the Win32 ConPTY API. New dependency, `pywinpty`, scoped to Windows only via a
  `sys_platform == "win32"` environment marker in `pyproject.toml` so it's simply absent from the
  Linux/macOS dependency graph.
- **`src/devtemplate/pty/bridge.py`** — `bridge_to_ssh_process(pty_proc: PtyProcess, process:
  asyncssh.SSHServerProcess) -> int`, an async function owning the full pump-loop, resize
  forwarding, and wait logic (detailed below). This is the one function that absorbs what would
  otherwise become several new underscore-prefixed helpers inside `ssh_server.py` — keeping that
  file's new surface to a three-line branch, not a pile of new private functions.

## `ssh_server.py` changes

`_handle_process` gains one branch at the top, before today's pipe-based path (which stays
completely unchanged below it):

```python
if process.get_terminal_type() is not None:
    width, height, _, _ = process.get_terminal_size()
    pty_proc = spawn_pty_process(
        [cli_binary, "exec", "-it", container_name, *shell_argv], rows=height, cols=width
    )
    return await bridge_to_ssh_process(pty_proc, process)
```

`process.get_terminal_type()` (asyncssh) returns the client's `TERM` string if a pty was
requested, `None` otherwise - orthogonal to shell-vs-exec, so `ssh -t <name> "top"` correctly
takes the pty path too, not just a bare `ssh <name>`.

### `bridge_to_ssh_process`

Bridges `pty_proc`'s blocking `.read()`/`.write()` into the asyncio server using the same shape
`ssh_server.py` already has proven in production for its outer stdio bridge
(`_pump_stdio_to_socket`'s daemon-thread-pair-plus-`socket.socketpair()` approach, including its
hard-won lesson of using raw `os.read`/`os.write` semantics rather than buffered I/O) — applied
here as a second, inner bridge (SSH channel <-> pty), not literally shared code with the outer
one, since the outer bridge is fd-based and this one is `PtyProcess`-object-based.

Two directions, structurally simpler than today's non-pty path since a pty merges stdout+stderr
into one stream (no need for the existing per-stream incremental UTF-8 decoder duplication):

- **Client -> pty:** reads `process.stdin`, same as today's `pump_client_to_process`, but the
  `TerminalSizeChanged` case is pulled out of the existing `_CHANNEL_EVENTS` ignore-and-continue
  group and forwarded: `pty_proc.resize(exc.height, exc.width)`. `BreakReceived`/`SignalReceived`
  keep today's ignore-and-continue behavior unchanged - those are explicit SSH protocol-level
  signal/break requests, distinct from a client typing Ctrl-C, which now arrives as an ordinary
  `0x03` byte in the data stream and flows straight through to the container's own pty (allocated
  by `-t`), exactly like `dvt ssh <name>`'s already-working direct-exec path.
- **pty -> client:** reads `pty_proc.read(...)`, writes to `process.stdout`.

Cleanup mirrors the existing pattern: `finally`-block cancellation of the pump task(s), thread
joins with the same drain-timeout philosophy already in place, closing the pty handle. Returns
`pty_proc.wait()`'s exit code, same contract `_handle_process` already has.

## stdout/stderr merging

Once a pty is in play, stdout and stderr are structurally merged into one stream - standard
terminal semantics (identical to SSHing into any real machine interactively, or to `dvt ssh
<name>`'s already-working direct path), not a regression. The "stdout/stderr stay separate"
guarantee in `docs/content/commands.md`'s "what's verified" section is specifically about
non-pty exec sessions, which never enter this new branch at all and keep today's behavior
byte-for-byte.

## Error handling

`spawn_pty_process` failing (pty allocation error, `pywinpty` unavailable) propagates up through
`bridge_to_ssh_process` and `_handle_process` to the existing `process_factory` error handler
already in `ssh_server.py` - which already prints a diagnostic to stderr and reports exit code
255 for any session-startup failure. No new error-handling path needed. Deliberately no silent
fallback to the old pipe-based behavior on pty failure, per the Non-goals above.

## Testing

- **`tests/test_pty_spawn.py`** (new): real, non-mocked tests against each backend, guarded with
  `pytest.mark.skipif` (no existing precedent for platform-gated tests in this codebase -
  `podman_machine.py`'s Windows-specific logic is tested via mocking rather than real OS calls -
  but genuine, unmocked OS pty syscalls only work on their own platform, so `skipif` is necessary
  here, not just a style choice; the `ubuntu-latest`/`macos-latest`/`windows-latest` CI matrix
  means both variants get real execution on every run regardless):
  - POSIX (`skipif(sys.platform == "win32")`): spawn a real `sh` subprocess via
    `spawn_pty_process`; assert `.read()` returns output. Critically: spawn
    `sh -c 'if [ -t 0 ]; then echo TTY; else echo NOTTY; fi'` and assert the output is `TTY` -
    this is the test that directly proves the bug this feature exists to fix. Also: `.write()`
    round-trips input correctly; `.resize()` changes what `stty size` reports inside the spawned
    shell; `.wait()` returns the right exit code for both a successful and a non-zero exit.
  - Windows (`skipif(sys.platform != "win32")`): same shape of assertions against the `pywinpty`
    backend, adapted to a Windows shell's own tty-detection idiom in place of `sh -c 'if [ -t 0
    ]...'`.
- **`tests/test_pty_bridge.py`** (new): mirrors `tests/test_ssh_server.py`'s existing style - a
  real `asyncssh` client connected to a real server, `process_factory` calling
  `bridge_to_ssh_process` directly against a real spawned `PtyProcess` (a stand-in command, not
  real Docker). Verifies: interactive round-trip data flows both directions; a client-side
  `TerminalSizeChanged` resize actually reaches the pty (assert via `stty size` again); session
  exit code propagates correctly; `BreakReceived`/`SignalReceived` are still just ignored
  (regression guard matching today's behavior).
- **`tests/test_ssh_server.py`** (existing file): one new test confirming `_handle_process`
  correctly branches on `process.get_terminal_type()` - a pty-requesting client reaches the new
  path (assert `spawn_pty_process`/`bridge_to_ssh_process` were called, via monkeypatch); a
  non-pty exec client stays on the existing pipe path, unchanged - the single most important
  regression test in this feature, since it's what proves the fix didn't touch the already-
  working, already-documented, already-tested exec path.

## Docs

`docs/content/commands.md`'s "SSH access: known v1 gaps" section loses its "No PTY is allocated
on the container side" bullet - moved to "what's verified" once implemented and manually
confirmed against a real container, matching the file's existing verified/not-yet-verified
structure - replaced with a note that pty-requesting sessions now get a real host-side pty
bridged through, while non-pty exec sessions are unchanged, and that the Windows implementation
depends on `pywinpty`.
