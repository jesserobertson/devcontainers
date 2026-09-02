from __future__ import annotations

import socket
import sys
import threading
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

import devtemplate.forward as forward_mod
from devtemplate.forward import (
    ForwardSpec,
    build_forwarder,
    relay_argv,
    select_relay_tool,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2718", ForwardSpec("127.0.0.1", 2718, "localhost", 2718)),
        ("8080:3000", ForwardSpec("127.0.0.1", 8080, "localhost", 3000)),
        ("9000:db:5432", ForwardSpec("127.0.0.1", 9000, "db", 5432)),
        ("0.0.0.0:8080:db:5432", ForwardSpec("0.0.0.0", 8080, "db", 5432)),
    ],
)
def test_parse_accepts_the_four_forms(text, expected):
    assert ForwardSpec.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "  ", "a:b:c:d:e", "2718:db:notaport", "notaport", "8080:db:", "8080::5432"],
)
def test_parse_rejects_malformed_specs(text):
    with pytest.raises(ValueError) as exc:
        ForwardSpec.parse(text)
    assert repr(text.strip()) in str(exc.value) or text.strip() in str(exc.value)


@given(
    st.integers(1, 65535),
    st.integers(1, 65535),
    st.sampled_from(["localhost", "db", "127.0.0.1", "api.internal"]),
    st.sampled_from(["127.0.0.1", "0.0.0.0"]),
)
def test_str_round_trips_through_parse(local, remote, host, bind):
    spec = ForwardSpec(bind, local, host, remote)
    assert ForwardSpec.parse(str(spec)) == spec


@pytest.mark.parametrize(
    "probe, expected",
    [
        ("/usr/bin/socat\n/usr/bin/nc\n", "socat"),
        ("/bin/nc\n", "nc"),
        ("/usr/local/bin/ncat\n/usr/bin/python3\n", "ncat"),
        ("/usr/bin/python3\n", "python3"),
        ("", None),
        ("\n\n", None),
    ],
)
def test_select_relay_tool_picks_first_available(probe, expected):
    assert select_relay_tool(probe) == expected


def test_relay_argv_socat_targets_remote_host_and_port():
    spec = ForwardSpec("127.0.0.1", 2718, "localhost", 2718)
    argv = relay_argv("socat", spec)
    assert argv[:2] == ["sh", "-c"]
    assert "TCP:localhost:2718" in argv[2]


def test_relay_argv_nc_uses_host_then_port():
    spec = ForwardSpec("127.0.0.1", 9000, "db", 5432)
    assert "nc db 5432" in relay_argv("nc", spec)[2]


def test_relay_argv_python3_embeds_host_and_port():
    spec = ForwardSpec("127.0.0.1", 8080, "api.internal", 3000)
    snippet = relay_argv("python3", spec)[2]
    assert "api.internal" in snippet and "3000" in snippet
    assert snippet.startswith("exec python3 -c ")


def test_relay_argv_rejects_unknown_tool():
    with pytest.raises(ValueError):
        relay_argv("telnet", ForwardSpec("127.0.0.1", 1, "localhost", 1))


def test_python_relay_script_compiles():
    from devtemplate.forward import _PYTHON_RELAY

    compile(_PYTHON_RELAY.format(host="db", port=5432), "<relay>", "exec")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _echo_server() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)

    def serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(
                target=lambda c: (
                    [c.sendall(d) for d in iter(lambda: c.recv(4096), b"")]
                    and c.close()
                ),
                args=(conn,),
                daemon=True,
            ).start()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


@pytest.fixture
def fake_container_env(monkeypatch):
    """Make build_forwarder resolve a running container and pick a relay tool
    whose 'exec -i' Popen is redirected to a local echo server instead of a
    real `podman exec`."""
    echo_srv, echo_port = _echo_server()
    monkeypatch.setattr(
        forward_mod,
        "find_workspace_container",
        lambda client, name: SimpleNamespace(name=f"dvt-{name}", status="running"),
    )
    # Probe: pretend the container has `socat`.
    monkeypatch.setattr(
        forward_mod, "_probe_relay_tool", lambda cli_binary, container: "socat"
    )
    real_popen = forward_mod.subprocess.Popen

    def fake_popen(argv, **kwargs):
        # argv == [cli_binary, "exec", "-i", container, "sh", "-c", snippet];
        # ignore it and just wire the pipes to the echo server via a plain
        # `python -c` TCP relay running on the host.
        relay = (
            "import socket,sys,threading;"
            f"s=socket.create_connection(('127.0.0.1',{echo_port}));"
            "threading.Thread(target=lambda:[s.sendall(x) for x in "
            "iter(lambda:sys.stdin.buffer.read1(4096),b'')] and s.shutdown(1),"
            "daemon=True).start();"
            "[ (sys.stdout.buffer.write(x),sys.stdout.buffer.flush()) for x in "
            "iter(lambda:s.recv(4096),b'')]"
        )
        return real_popen([sys.executable, "-c", relay], **kwargs)

    monkeypatch.setattr(forward_mod.subprocess, "Popen", fake_popen)
    yield
    echo_srv.close()


def test_build_forwarder_round_trips_bytes(fake_container_env):
    port = _free_port()
    fwd = build_forwarder(object(), "podman", "ws", [str(port)]).unwrap()
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(b"ping-through-tunnel")
        assert c.recv(4096) == b"ping-through-tunnel"
        c.close()
    finally:
        fwd.close()


def test_build_forwarder_close_is_idempotent(fake_container_env):
    fwd = build_forwarder(object(), "podman", "ws", [str(_free_port())]).unwrap()
    fwd.close()
    fwd.close()
    assert all(listener.fileno() == -1 for listener in fwd._listeners)


def test_build_forwarder_errs_when_container_not_running(monkeypatch):
    monkeypatch.setattr(forward_mod, "find_workspace_container", lambda c, n: None)
    result = build_forwarder(object(), "podman", "ws", ["2718"])
    assert result.is_err()
    assert "not running" in str(result.unwrap_err()) or "running" in str(
        result.unwrap_err()
    )


def test_build_forwarder_errs_on_local_port_in_use(fake_container_env):
    busy = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    taken = busy.getsockname()[1]
    try:
        result = build_forwarder(object(), "podman", "ws", [str(taken)])
        assert result.is_err()
        assert str(taken) in str(result.unwrap_err())
    finally:
        busy.close()


def test_build_forwarder_errs_when_no_relay_tool(monkeypatch):
    monkeypatch.setattr(
        forward_mod,
        "find_workspace_container",
        lambda c, n: SimpleNamespace(name="dvt-ws", status="running"),
    )
    monkeypatch.setattr(
        forward_mod, "_probe_relay_tool", lambda cli_binary, container: None
    )
    result = build_forwarder(object(), "podman", "ws", ["2718"])
    assert result.is_err()
    assert "socat" in str(result.unwrap_err())
