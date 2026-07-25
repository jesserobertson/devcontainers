from __future__ import annotations

import asyncio
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
