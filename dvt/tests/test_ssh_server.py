from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncssh
import pytest

from devtemplate.ssh_server import _handle_process, _NoAuthServer, run_stdio_server

# A stand-in for `docker exec -i <name> sh`: reads everything on stdin, echoes it
# back with a prefix, then exits non-zero so the exit-code wiring is observable.
_FAKE_CONTAINER_SHELL = (
    "import sys;"
    "data = sys.stdin.buffer.read();"
    "sys.stdout.buffer.write(b'from-container:' + data);"
    "sys.stdout.buffer.flush();"
    "sys.exit(7)"
)

# A stand-in for `docker exec -i <name> sh -c "<command>"`: a minimal `sh -c`
# that understands `echo`, so a requested command genuinely *runs* and its
# output has to travel back over the SSH channel. Exits non-zero so the exec
# path's exit-code propagation is observable too.
_FAKE_SH_C = (
    "import sys;"
    "cmd = sys.argv[1];"
    "assert cmd.startswith('echo '), cmd;"
    # Binary write: text mode would translate \n to \r\n on Windows, which is
    # the stand-in's artifact rather than anything the bridge does.
    "sys.stdout.buffer.write(cmd[len('echo '):].encode() + b'\\n');"
    "sys.stdout.buffer.flush();"
    "sys.exit(3)"
)

# Line-oriented echo, so the test can keep a session open across several
# exchanges rather than reading once until EOF.
_FAKE_ECHO_SHELL = (
    "import sys\n"
    "while True:\n"
    "    line = sys.stdin.buffer.readline()\n"
    "    if not line:\n"
    "        break\n"
    "    sys.stdout.buffer.write(b'echo:' + line)\n"
    "    sys.stdout.buffer.flush()\n"
)

# Writes to *both* of its own streams, with text that can't be confused for the
# other one. Stands in for the real thing this protects: the spawned process is
# the docker/podman CLI wrapper, which emits its own warnings on stderr while
# the container's actual command output goes to stdout.
_FAKE_BOTH_STREAMS_SHELL = (
    "import sys;"
    "sys.stderr.buffer.write(b'STDERR-ONLY-marker\\n');"
    "sys.stderr.buffer.flush();"
    "sys.stdout.buffer.write(b'STDOUT-ONLY-marker\\n');"
    "sys.stdout.buffer.flush();"
    "sys.exit(0)"
)

# Emits ASCII padding chosen so the *first* 4096-byte read lands one byte into a
# 3-byte character, then a long run of the same character so essentially every
# subsequent read boundary splits one too (4096 is not a multiple of 3). A
# per-chunk `bytes.decode()` mangles every one of those into replacement
# characters; an incremental decoder carries the partial sequence over.
_UTF8_PAD_LENGTH = 4095
_UTF8_REPEATS = 10000
_FAKE_UTF8_SHELL = (
    "import sys;"
    f"payload = ('A' * {_UTF8_PAD_LENGTH} + '\\u65e5' * {_UTF8_REPEATS})"
    ".encode('utf-8');"
    "sys.stdout.buffer.write(payload);"
    "sys.stdout.buffer.flush();"
    "sys.exit(0)"
)

# Outlives the client: still running when the connection is aborted, so the
# server-side pumps are mid-flight when the channel dies.
_FAKE_SLOW_SHELL = (
    "import sys, time;"
    "time.sleep(0.6);"
    "sys.stdout.buffer.write(b'done');"
    "sys.stdout.buffer.flush();"
    "sys.exit(5)"
)


def _patch_exec_to_fake_shell(monkeypatch, expected_cmd):
    """Redirect `_handle_process`'s docker/podman spawn to a stand-in Python
    process, so no container runtime is needed. Only the *argv* is swapped -
    a real subprocess with real OS pipes is still spawned, so the bridging
    this task exists to prove stays under test."""
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object):
        assert cmd == expected_cmd
        return await real_create_subprocess_exec(
            sys.executable, "-c", _FAKE_CONTAINER_SHELL, **kwargs
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


def _patch_exec_to_fake_sh_c(monkeypatch) -> list[tuple[str, ...]]:
    """Same idea as `_patch_exec_to_fake_shell`, but the stand-in acts as
    `sh -c` and is handed whatever command `_handle_process` decided to run.
    Returns a list that records the argv actually spawned, so the test can
    assert on it directly rather than from inside a background task."""
    real_create_subprocess_exec = asyncio.create_subprocess_exec
    spawned: list[tuple[str, ...]] = []

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object):
        spawned.append(cmd)
        return await real_create_subprocess_exec(
            sys.executable, "-c", _FAKE_SH_C, cmd[-1], **kwargs
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    return spawned


def _patch_exec_to_source(monkeypatch, source: str) -> None:
    """Redirect the docker/podman spawn to a stand-in running `source`. Same
    contract as `_patch_exec_to_fake_shell`: only the argv changes, the
    subprocess and its OS pipes are real."""
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object):
        return await real_create_subprocess_exec(sys.executable, "-c", source, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


def _patch_stdio_to_pipe_bridge(monkeypatch, peer_sock: socket.socket) -> None:
    """Monkeypatch `sys.stdin`/`sys.stdout` to real OS pipes bridged to
    `peer_sock` by background threads, instead of a bare socket standing in
    for them directly. `peer_sock` is the near-side socket of the pair
    connected to the fake SSH client (i.e. what a real ProxyCommand's stdin/
    stdout pipes would be relaying to/from).

    `_pump_stdio_to_socket` reads/writes stdio via raw fd-level `os.read`/
    `os.write` on `sys.stdin.fileno()`/`sys.stdout.fileno()` (see its
    docstring: a real `ssh` client's `ProxyCommand` hands this process genuine
    pipes, and Python's *buffered* io over those pipes turned out to misbehave
    badly on Windows - a bug this exact fd-level approach was written to fix).
    A bare socket's fileno() wouldn't exercise that fd-based path faithfully
    (`os.read`/`os.write` on a raw Windows socket handle behave differently
    from a real pipe), so this stands up genuine `os.pipe()` pairs and
    shuttles bytes to/from `peer_sock` by hand - mirroring what a real
    ProxyCommand's inherited stdio actually looks like.
    """
    stdin_read_fd, stdin_write_fd = os.pipe()
    stdout_read_fd, stdout_write_fd = os.pipe()

    def peer_to_stdin() -> None:
        try:
            while True:
                data = peer_sock.recv(4096)
                if not data:
                    break
                # os.write() can write short; loop until it's all sent (see
                # the matching comment in ssh_server._pump_stdio_to_socket).
                while data:
                    data = data[os.write(stdin_write_fd, data) :]
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                os.close(stdin_write_fd)

    def stdout_to_peer() -> None:
        try:
            while True:
                data = os.read(stdout_read_fd, 4096)
                if not data:
                    break
                peer_sock.sendall(data)
        except OSError:
            pass

    threading.Thread(target=peer_to_stdin, daemon=True).start()
    threading.Thread(target=stdout_to_peer, daemon=True).start()

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: stdin_read_fd))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(fileno=lambda: stdout_write_fd))


async def _serve(sock, host_key, process_factory) -> asyncssh.SSHServerConnection:
    """`asyncssh.run_server` is wrapped in an async-context-manager helper, so it
    returns an `_ACMWrapper` rather than a bare coroutine - awaitable, but not
    something `asyncio.create_task` accepts directly. This wraps it back into a
    real coroutine so the server handshake can run concurrently with the
    client's."""
    return await asyncssh.run_server(
        sock,
        server_factory=_NoAuthServer,
        server_host_keys=[host_key],
        process_factory=process_factory,
        gss_host=None,  # Matches run_stdio_server; see the comment there.
    )


@pytest.mark.asyncio
async def test_server_and_client_exchange_bytes_over_a_real_ssh_session():
    """Proves the SSH protocol negotiation and the process-bridging plumbing
    both genuinely work: a real asyncssh client connects to a real asyncssh
    server over a real (loopback, in-process) socket pair, requests a shell,
    and the bridged subprocess's output round-trips back to the client. This
    is exactly the class of bug a fully-mocked test cannot catch - the
    original ProxyCommand design looked correct in isolation and only failed
    when a real ssh client actually tried to negotiate the protocol.
    """
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        # Stand-in for the real docker-exec bridge (exercised for real by
        # test_handle_process_bridges_a_real_subprocess below) - just echoes
        # one line back, proving stdin -> process -> stdout round-trips
        # through the real SSH channel.
        line = await process.stdin.readline()
        process.stdout.write(f"echo:{line}")
        process.exit(0)

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process()
            process.stdin.write("hello\n")
            process.stdin.write_eof()
            output = await process.stdout.read()

    assert output == "echo:hello\n"
    (await server_task).close()


@pytest.mark.asyncio
async def test_handle_process_bridges_a_real_subprocess_and_returns_its_exit_code(
    monkeypatch,
):
    """The same real-client/real-server exchange, but the server side runs the
    *actual* `_handle_process` bridge. Only the argv translation is patched
    (so no docker/podman daemon is needed) - the subprocess it spawns, its
    OS pipes, and the SSH channel are all real. Asserts both directions of
    the byte flow and that `_handle_process` hands its subprocess's exit code
    back to its caller (the wiring `run_stdio_server` depends on).
    """
    _patch_exec_to_fake_shell(
        monkeypatch, ("docker", "exec", "-i", "myws", "sh", "-c", 'exec "${SHELL:-sh}"')
    )

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()
    returned: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        returned.append(await _handle_process(process, "docker", "myws"))

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process()
            process.stdin.write("hello\n")
            process.stdin.write_eof()
            result = await process.wait()

    assert result.stdout == "from-container:hello\n"
    assert result.exit_status == 7
    assert returned == [7]
    (await server_task).close()


@pytest.mark.asyncio
async def test_non_interactive_exec_request_runs_the_requested_command(monkeypatch):
    """A non-interactive exec request (`ssh host "echo hello-from-real-ssh"` -
    exactly the shape of this feature's end-to-end integration test) must
    actually run the requested command, not silently drop the client into an
    interactive shell that ignores it. asyncssh surfaces the distinction as
    `SSHServerProcess.command`: None for a bare shell request, the command
    line for an exec request. Real client, real server, real subprocess - only
    the docker/podman argv is redirected.
    """
    spawned = _patch_exec_to_fake_sh_c(monkeypatch)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()
    returned: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        returned.append(await _handle_process(process, "docker", "myws"))

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process("echo hello-from-real-ssh")
            result = await process.wait()

    # The command reached the container's `sh -c` as its own argv entry...
    assert spawned == [
        ("docker", "exec", "-i", "myws", "sh", "-c", "echo hello-from-real-ssh")
    ]
    # ...actually ran there, and its output round-tripped back to the client.
    assert result.stdout == "hello-from-real-ssh\n"
    assert result.exit_status == 3
    assert returned == [3]
    (await server_task).close()


@pytest.mark.asyncio
async def test_input_still_reaches_the_container_after_a_terminal_resize(monkeypatch):
    """A terminal resize must not break the session. OpenSSH sends a
    `window-change` request on every resize, and asyncssh delivers it by
    *raising* `TerminalSizeChanged` out of `process.stdin.read()` - so handling
    it as though it were end-of-input silently kills the user's keystrokes for
    the rest of the session (in practice it closed the container's stdin, which
    ended the shell and tore the whole session down).

    Real client, real server, real subprocess, and a real `window-change` sent
    via asyncssh's own `change_terminal_size`.
    """
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object):
        return await real_create_subprocess_exec(
            sys.executable, "-c", _FAKE_ECHO_SHELL, **kwargs
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        await _handle_process(process, "docker", "myws")

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process()
            process.stdin.write("one\n")
            assert await process.stdout.readline() == "echo:one\n"

            # The event under test - a genuine window-change request.
            process.change_terminal_size(100, 40)
            await asyncio.sleep(0.2)

            # Pre-fix this raised BrokenPipeError("Channel not open for
            # sending"): the resize had already torn the session down.
            process.stdin.write("two\n")
            assert await process.stdout.readline() == "echo:two\n"

            # Close the session down cleanly so the echo subprocess exits and
            # is reaped while the loop is still running.
            process.stdin.write_eof()
            await process.wait()

    (await server_task).close()


@pytest.mark.asyncio
async def test_client_vanishing_mid_session_still_yields_the_container_exit_code(
    monkeypatch,
):
    """A client that disappears mid-session (network drop, Gateway killed)
    surfaces on the server as `asyncssh.ConnectionLost` raised out of
    `process.stdin.read()`. That must not escape `_handle_process`: it runs as
    a task awaited during teardown, so an exception there skips the exit-status
    reporting entirely and the session silently becomes a success.

    Real client, real server, real subprocess; the disconnect is a real
    `conn.abort()`, not a simulated one.
    """
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*cmd: str, **kwargs: object):
        return await real_create_subprocess_exec(
            sys.executable, "-c", _FAKE_SLOW_SHELL, **kwargs
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()
    outcome: list[object] = []
    finished = asyncio.Event()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        try:
            outcome.append(await _handle_process(process, "docker", "myws"))
        except BaseException as exc:  # noqa: BLE001 - recording, not handling
            outcome.append(f"raised {type(exc).__name__}")
        finally:
            finished.set()

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        conn = await asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        )
        await conn.create_process()
        await asyncio.sleep(0.1)  # let the session settle before pulling the rug
        conn.abort()
        await finished.wait()

    # Pre-fix this was ["raised ConnectionLost"], which Finding 1's path then
    # turned into a silent exit code 0.
    assert outcome == [5], "a mid-session disconnect must still report the exit code"
    (await server_task).close()


@pytest.mark.asyncio
async def test_run_stdio_server_bridges_stdin_stdout_to_a_real_ssh_session(monkeypatch):
    """Exercises `run_stdio_server` as a black box - it is called exactly as
    `dvt ssh --stdio` will call it, and driven only through `sys.stdin` /
    `sys.stdout`, which is the interface a `ProxyCommand` gives it. Those are
    wired to a socket a real asyncssh client speaks SSH over, so this covers
    the one thing Step 2's tests don't: the blocking-I/O thread bridge between
    this process's real stdio and the server's internal socketpair. Nothing in
    asyncssh is mocked; only the docker/podman argv is redirected to a
    stand-in shell so no container runtime is required.
    """
    _patch_exec_to_fake_shell(
        monkeypatch, ("podman", "exec", "-i", "proj", "sh", "-c", 'exec "${SHELL:-sh}"')
    )

    stdio_end, client_end = socket.socketpair()
    _patch_stdio_to_pipe_bridge(monkeypatch, stdio_end)

    returned: list[int] = []
    server_thread = threading.Thread(
        target=lambda: returned.append(run_stdio_server("podman", "proj")),
        daemon=True,
    )
    server_thread.start()

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_end, host="dvt-test-client", known_hosts=None, username="anyone"
        ) as conn:
            process = await conn.create_process()
            process.stdin.write("hello\n")
            process.stdin.write_eof()
            result = await process.wait()

    assert result.stdout == "from-container:hello\n"
    assert result.exit_status == 7

    server_thread.join(timeout=30)
    assert not server_thread.is_alive(), "run_stdio_server did not return"
    assert returned == [7], "run_stdio_server must return the container's exit code"


@pytest.mark.asyncio
async def test_run_stdio_server_reports_a_failed_session_as_non_zero(
    monkeypatch, capsys
):
    """A session that never gets off the ground - here the most likely real
    cause, a container CLI that isn't on PATH - must not look like a clean
    exit. asyncssh only logs a failing process_factory at debug level and
    force-closes the connection, so without explicit handling `run_stdio_server`
    would return 0 and a totally broken ProxyCommand would be indistinguishable
    from a working one.

    Nothing is patched here at all: the SSH side is real, and the spawn really
    does fail because the binary genuinely does not exist.
    """
    stdio_end, client_end = socket.socketpair()
    _patch_stdio_to_pipe_bridge(monkeypatch, stdio_end)

    returned: list[int] = []
    missing = "dvt-no-such-container-cli"
    server_thread = threading.Thread(
        target=lambda: returned.append(run_stdio_server(missing, "proj")),
        daemon=True,
    )
    server_thread.start()

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_end, host="dvt-test-client", known_hosts=None, username="anyone"
        ) as conn:
            with contextlib.suppress(asyncssh.Error):
                # The session dies on the server side; the client sees either a
                # 255 exit status or the channel/connection being torn down.
                process = await conn.create_process()
                await process.wait()

    server_thread.join(timeout=30)
    assert not server_thread.is_alive(), "run_stdio_server did not return"
    assert returned == [255], "a failed session must not report success"
    # And it must say why, on stderr - stdout carries the SSH stream.
    assert missing in capsys.readouterr().err


@pytest.mark.asyncio
async def test_container_stderr_arrives_on_the_clients_stderr_not_its_stdout(
    monkeypatch,
):
    """The bridged process's stderr must reach the client on SSH's own
    extended-data stderr channel, kept distinct from stdout.

    The process being spawned is the `docker`/`podman` CLI wrapper, and it
    writes its own diagnostics to stderr - podman's `WARN[0000]...` lines are
    routine. Merging the two (the previous `stderr=asyncio.subprocess.STDOUT`)
    silently injects those into the *command's* stdout, which is exactly what
    VS Code Remote-SSH and JetBrains Gateway parse to drive a remote host.

    Real client, real server, real subprocess writing to two real OS pipes;
    only the docker/podman argv is redirected.
    """
    _patch_exec_to_source(monkeypatch, _FAKE_BOTH_STREAMS_SHELL)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        await _handle_process(process, "docker", "myws")

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process()
            process.stdin.write_eof()
            result = await process.wait()

    # Each stream carries its own output and *only* its own output. Pre-fix the
    # stderr marker turned up inside result.stdout and result.stderr was empty.
    assert result.stdout == "STDOUT-ONLY-marker\n"
    assert result.stderr == "STDERR-ONLY-marker\n"
    (await server_task).close()


@pytest.mark.asyncio
async def test_multibyte_utf8_split_across_read_boundaries_survives(monkeypatch):
    """A multi-byte UTF-8 character straddling a read boundary must arrive
    intact.

    The client-bound pump reads the subprocess in fixed 4096-byte chunks. The
    previous per-chunk `chunk.decode(errors="replace")` had no memory across
    reads, so any character whose bytes spanned a boundary was destroyed into
    replacement characters - ordinary damage for accented text, box drawing or
    non-ASCII filenames as soon as output exceeds one chunk.

    The stand-in emits 4095 ASCII bytes and then a long run of a 3-byte
    character, so the first read ends one byte into a character and (4096 not
    being a multiple of 3) essentially every later read does too. Real client,
    real server, real subprocess; only the docker/podman argv is redirected.
    """
    _patch_exec_to_source(monkeypatch, _FAKE_UTF8_SHELL)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        await _handle_process(process, "docker", "myws")

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock,
            host="dvt-test-client",
            known_hosts=None,
            username="anyone",
        ) as conn:
            process = await conn.create_process()
            process.stdin.write_eof()
            result = await process.wait()

    # Escapes rather than literals throughout, so this file stays pure ASCII
    # and can't be broken by an editor or checkout re-encoding it.
    expected = "A" * _UTF8_PAD_LENGTH + "\u65e5" * _UTF8_REPEATS
    # Checked first and named: pre-fix this failed with thousands of U+FFFD, and
    # a bare equality assertion over 34k characters is unreadable when it fails.
    assert "\ufffd" not in result.stdout, "a chunk boundary corrupted a character"
    assert result.stdout == expected
    (await server_task).close()


@pytest.mark.asyncio
async def test_handle_process_uses_the_pty_bridge_when_a_pty_was_requested(monkeypatch):
    """process.get_terminal_type() is asyncssh's own signal that the client
    asked for a pty (ssh <name>, or ssh -t <name> <cmd>) - _handle_process
    must route to the new pty bridge in that case, not the plain-pipe path.
    Only the two devtemplate.pty entry points are mocked; nothing about
    asyncssh or the branching logic itself is."""
    import devtemplate.ssh_server as ssh_server_module

    fake_pty_proc = object()
    spawn_calls: list[tuple[list[str], int, int]] = []

    def fake_spawn(argv, rows, cols):
        spawn_calls.append((argv, rows, cols))
        return fake_pty_proc

    bridge_calls: list[tuple[object, object]] = []

    async def fake_bridge(pty_proc, process):
        bridge_calls.append((pty_proc, process))
        return 0

    monkeypatch.setattr(ssh_server_module, "spawn_pty_process", fake_spawn)
    monkeypatch.setattr(ssh_server_module, "bridge_to_ssh_process", fake_bridge)

    fake_process = MagicMock()
    fake_process.get_terminal_type.return_value = "xterm"
    fake_process.get_terminal_size.return_value = (80, 24, 0, 0)
    fake_process.command = None

    result = await ssh_server_module._handle_process(fake_process, "docker", "myws")

    assert result == 0
    assert spawn_calls == [
        (["docker", "exec", "-it", "myws", "sh", "-c", 'exec "${SHELL:-sh}"'], 24, 80)
    ]
    assert bridge_calls == [(fake_pty_proc, fake_process)]


@pytest.mark.asyncio
async def test_non_pty_exec_session_still_uses_the_plain_pipe_path(monkeypatch):
    """The single most important regression test in this feature: a client
    that does NOT request a pty (ssh <name> "cmd", what VS Code Remote-SSH/
    JetBrains Gateway rely on) must be completely unaffected by this
    change - same argv (-i, not -it), same separate-stdout/stderr pipes.
    Reuses the existing real-subprocess pattern below, not a mock, to prove
    the actual pipe path still runs end to end."""
    spawned = _patch_exec_to_fake_sh_c(monkeypatch)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()
    returned: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        returned.append(await _handle_process(process, "docker", "myws"))

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock, host="dvt-test-client", known_hosts=None, username="anyone"
        ) as conn:
            # No term_type= passed to create_process(): matches a plain,
            # non-pty exec request exactly.
            process = await conn.create_process("echo hello-from-real-ssh")
            result = await process.wait()

    assert spawned == [
        ("docker", "exec", "-i", "myws", "sh", "-c", "echo hello-from-real-ssh")
    ]
    assert result.stdout == "hello-from-real-ssh\n"
    assert result.exit_status == 3
    assert returned == [3]
    (await server_task).close()
