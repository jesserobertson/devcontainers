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
