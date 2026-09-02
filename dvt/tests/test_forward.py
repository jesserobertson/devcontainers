from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from devtemplate.forward import ForwardSpec


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


from devtemplate.forward import relay_argv, select_relay_tool


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
