from __future__ import annotations

import socket
import threading
import time

import pytest

from devtemplate.net import bounded_socketpair

pytestmark = pytest.mark.unit


def test_bounded_socketpair_returns_a_working_connected_pair():
    a, b = bounded_socketpair()
    try:
        a.sendall(b"hello")
        assert b.recv(5) == b"hello"
    finally:
        a.close()
        b.close()


def test_bounded_socketpair_retries_past_a_transient_failure(monkeypatch):
    """A single OSError from one attempt (e.g. a lost race for an ephemeral
    port) must not fail the whole call - only exhausting every attempt
    should."""
    real_socketpair = socket.socketpair
    calls = {"n": 0}

    def flaky_socketpair():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated transient pairing failure")
        return real_socketpair()

    monkeypatch.setattr(socket, "socketpair", flaky_socketpair)

    a, b = bounded_socketpair(timeout=1.0, attempts=3)
    try:
        assert calls["n"] == 2
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    finally:
        a.close()
        b.close()


def test_bounded_socketpair_gives_up_after_exhausting_attempts_that_hang(monkeypatch):
    """Reproduces the actual defect this module exists to fix: a bare
    `socket.socketpair()` call that never returns (observed for real on
    Windows under system load, see bounded_socketpair's docstring) must not
    be allowed to hang the caller forever - it must surface as a bounded,
    diagnosable TimeoutError instead."""

    def hangs_forever():
        threading.Event().wait()  # never set; blocks until the thread dies with the process
        raise AssertionError("unreachable")

    monkeypatch.setattr(socket, "socketpair", hangs_forever)

    start = time.monotonic()
    with pytest.raises(TimeoutError, match="did not succeed after 2 attempts"):
        bounded_socketpair(timeout=0.2, attempts=2)
    elapsed = time.monotonic() - start

    # Bounded: two attempts at 0.2s each, not an indefinite hang.
    assert elapsed < 2.0
