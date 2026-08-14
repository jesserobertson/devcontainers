"""Bridges one opened SSH session to a docker/podman exec subprocess - the
pty branch delegates to devtemplate.pty, the non-pty branch runs its own
plain-pipe pumps here. The pty-requesting-session counterpart to this
module is devtemplate.pty.bridge.bridge_to_ssh_process; see that module's
own docstring for the other half of this split."""

from __future__ import annotations

import asyncio
import codecs
import contextlib

import asyncssh

from devtemplate.pty import CHUNK, bridge_to_ssh_process, spawn_pty_process

__all__ = ["handle_process"]

CHANNEL_EVENTS = (
    asyncssh.misc.TerminalSizeChanged,
    asyncssh.misc.BreakReceived,
    asyncssh.misc.SignalReceived,
)
"""Out-of-band channel events asyncssh delivers by *raising* them out of a
stream read (see `stream.py`'s `exception_received`) instead of returning data.

Each means "nothing to forward this time, keep reading" - emphatically *not*
"this stream is finished". asyncssh accepts pty requests by default, and
OpenSSH sends a `window-change` on every terminal resize, so treating one of
these as end-of-input would kill the user's keystrokes for the rest of an
ordinary interactive session the first time they resized their window.

`SoftEOFReceived` is deliberately absent: asyncssh converts it into a normal
empty read internally and never raises it here. All of these derive from
`Exception` directly rather than `asyncssh.Error`, so they need listing
separately from genuine protocol failures. NOT shared with
devtemplate.pty.bridge's own CHANNEL_EVENTS - see this package's own
Global Constraints note (in the plan this code came from) for why the two
tuples differ."""


async def handle_process(
    process: asyncssh.SSHServerProcess, cli_binary: str, container_name: str
) -> int:
    """Bridge one opened SSH session to a `docker/podman exec -i` subprocess -
    the same exec mechanism `exec_interactive` already uses, just with pipes
    instead of inherited stdio (asyncssh owns the actual terminal now).

    Honours both kinds of session an SSH client can ask for. `process.command`
    is `None` for a bare shell request (`ssh host`) and carries the requested
    command line for an exec request (`ssh host "echo hi"`); the latter must
    actually run that command rather than dropping the client into an
    interactive shell that ignores it. Tools driving this as a `ProxyCommand`
    - JetBrains Gateway especially - rely almost entirely on exec requests.

    Reports the subprocess's exit status to the SSH client *and* returns it,
    so the caller can use it as its own process exit code. `process.exit()`
    only sets the SSH channel's status; it hands nothing back to Python.

    If the client requested a pty (`process.get_terminal_type()` is not
    `None`), bridges to a real host-side pseudo-terminal instead (see
    `devtemplate.pty`) - `-it` rather than `-i`, so the container's shell
    gets a real tty. Non-pty exec requests are completely unaffected by this
    branch.
    """
    # A bare shell request runs the container user's own configured shell
    # (falling back to sh if $SHELL isn't set) rather than hardcoding sh -
    # images that wire up shell-startup hooks (e.g. a pixi project's
    # `pixi shell-hook` in .bashrc/fish's conf.d) only fire under that real
    # shell. An exec request's command is passed as its own distinct argv
    # entry, reaching the container's `sh -c` exactly as the client wrote it -
    # no shell runs on this side.
    shell_argv = (
        ["sh", "-c", 'exec "${SHELL:-sh}"']
        if process.command is None
        else ["sh", "-c", process.command]
    )

    term_type = process.get_terminal_type()
    if term_type is not None:
        width, height, _, _ = process.get_terminal_size()
        # A client may legally request a pty without stating its dimensions,
        # in which case RFC 4254 says the zero values must be ignored -
        # asyncssh's own client does exactly this unless given term_size, and
        # get_terminal_size() then reports (0, 0, 0, 0). The POSIX backend
        # tolerates a 0x0 pty, but ConPTY rejects it outright ("PTY cols and
        # rows must be positive and non-zero"), and with this feature's
        # deliberate no-fallback policy that would kill the session with exit
        # 255 - reproducing the very "session looks broken" bug this branch
        # exists to fix, for a client that did nothing wrong. 80x24 is the
        # conventional default an unsized terminal gets.
        #
        # -e TERM=... is passed explicitly because docker/podman exec -it
        # otherwise defaults the container's TERM to xterm regardless of what
        # the client actually asked for - a real sshd forwards the client's
        # TERM, and without this a client on xterm-256color (or similar)
        # silently loses color capability in full-screen programs like
        # vim/htop inside the container.
        pty_proc = spawn_pty_process(
            [
                cli_binary,
                "exec",
                "-it",
                "-e",
                f"TERM={term_type}",
                container_name,
                *shell_argv,
            ],
            rows=height or 24,
            cols=width or 80,
        )
        return await bridge_to_ssh_process(pty_proc, process)

    proc = await asyncio.create_subprocess_exec(
        cli_binary,
        "exec",
        "-i",
        container_name,
        *shell_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Kept *separate* from stdout, and pumped onto SSH's own extended-data
        # stderr channel below. Merging the two (stderr=STDOUT) looks like
        # harmless `2>&1` fidelity until you remember what is actually being
        # spawned: the docker/podman CLI wrapper, which emits its own warnings
        # (podman's `WARN[0000]...` lines are routine) on stderr. Merged, those
        # land inside the *command's* stdout - silently corrupting it for any
        # tool that parses an exec'd command's output, which is exactly how
        # VS Code Remote-SSH and JetBrains Gateway drive a remote host.
        stderr=asyncio.subprocess.PIPE,
    )
    proc_stdin = proc.stdin
    proc_stdout = proc.stdout
    proc_stderr = proc.stderr
    assert proc_stdin is not None
    assert proc_stdout is not None
    assert proc_stderr is not None

    async def pump_client_to_process() -> None:
        # Only two things genuinely end this direction: the container's shell
        # exiting (OSError/BrokenPipeError) or the client vanishing mid-session
        # (asyncssh.ConnectionLost and friends, under asyncssh.Error). Neither
        # may escape - this runs as a task the session teardown awaits, and an
        # exception here would skip reporting the session's exit status.
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                try:
                    data = await process.stdin.read(CHUNK)
                except CHANNEL_EVENTS:
                    # Handled per-read, not around the loop: these are events,
                    # not end-of-input, and must not stop us forwarding.
                    continue
                if not data:
                    break
                proc_stdin.write(data.encode() if isinstance(data, str) else data)
                await proc_stdin.drain()
        with contextlib.suppress(OSError):
            proc_stdin.close()

    async def pump_process_to_client(
        source: asyncio.StreamReader, sink: asyncssh.SSHWriter[str]
    ) -> None:
        # Same reasoning in the other direction: once the channel is gone there
        # is nowhere to put the container's output, which ends this pump but
        # still leaves a real exit code for `proc.wait()` to report. No
        # CHANNEL_EVENTS here - those come from reading the *client* stream,
        # and this pump reads the subprocess.
        #
        # The decoder is incremental, and deliberately created *per call* so
        # the stdout and stderr pumps never share one: `chunk.decode()` on each
        # raw read destroys any multi-byte UTF-8 character straddling a read
        # boundary (a 3-byte character split 1/2 across two reads becomes two
        # replacement characters), which output longer than one `CHUNK` hits
        # routinely - accented text, box drawing, non-ASCII filenames. An
        # incremental decoder holds the partial sequence over to the next read
        # and reassembles it. Feeding one decoder from two interleaved streams
        # would splice their partial sequences together instead.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        with contextlib.suppress(asyncssh.Error, OSError):
            while True:
                chunk = await source.read(CHUNK)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    sink.write(text)
            # Flush whatever a truncated final sequence left pending, so a
            # stream ending mid-character still terminates deterministically.
            tail = decoder.decode(b"", final=True)
            if tail:
                sink.write(tail)

    # The client half is a background task rather than a `gather` partner: a
    # client is under no obligation to ever send EOF, so waiting on it would
    # hang every session whose shell exits on its own. The two output pumps
    # *are* gathered: both must drain fully before the exit status is reported,
    # or the tail of either stream races the channel closing.
    client_pump = asyncio.create_task(pump_client_to_process())
    try:
        await asyncio.gather(
            pump_process_to_client(proc_stdout, process.stdout),
            pump_process_to_client(proc_stderr, process.stderr),
        )
        exit_code = await proc.wait()
    finally:
        # Belt and braces: the pump suppresses its own I/O failures, but this
        # runs in a `finally`, so anything escaping here would replace the real
        # outcome (including a failure being reported) with itself.
        client_pump.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await client_pump

    process.exit(exit_code)
    return exit_code
