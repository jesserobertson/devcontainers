from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
import threading
from types import SimpleNamespace

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
            sock=client_sock, known_hosts=None, username="anyone"
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
    _patch_exec_to_fake_shell(monkeypatch, ("docker", "exec", "-i", "myws", "sh"))

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server_sock, client_sock = socket.socketpair()
    returned: list[int] = []

    async def process_factory(process: asyncssh.SSHServerProcess) -> None:
        returned.append(await _handle_process(process, "docker", "myws"))

    server_task = asyncio.create_task(_serve(server_sock, host_key, process_factory))

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_sock, known_hosts=None, username="anyone"
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
            sock=client_sock, known_hosts=None, username="anyone"
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
            sock=client_sock, known_hosts=None, username="anyone"
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
            sock=client_sock, known_hosts=None, username="anyone"
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
    _patch_exec_to_fake_shell(monkeypatch, ("podman", "exec", "-i", "proj", "sh"))

    stdio_end, client_end = socket.socketpair()
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=stdio_end.makefile("rb", buffering=0))
    )
    monkeypatch.setattr(
        sys, "stdout", SimpleNamespace(buffer=stdio_end.makefile("wb", buffering=0))
    )

    returned: list[int] = []
    server_thread = threading.Thread(
        target=lambda: returned.append(run_stdio_server("podman", "proj")),
        daemon=True,
    )
    server_thread.start()

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_end, known_hosts=None, username="anyone"
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
    monkeypatch.setattr(
        sys, "stdin", SimpleNamespace(buffer=stdio_end.makefile("rb", buffering=0))
    )
    monkeypatch.setattr(
        sys, "stdout", SimpleNamespace(buffer=stdio_end.makefile("wb", buffering=0))
    )

    returned: list[int] = []
    missing = "dvt-no-such-container-cli"
    server_thread = threading.Thread(
        target=lambda: returned.append(run_stdio_server(missing, "proj")),
        daemon=True,
    )
    server_thread.start()

    async with asyncio.timeout(30):
        async with asyncssh.connect(
            sock=client_end, known_hosts=None, username="anyone"
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
