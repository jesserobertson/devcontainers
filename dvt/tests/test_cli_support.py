from __future__ import annotations

import json

import pytest
import typer
from logerr import Err, Ok
from rich.console import Console

from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status


def test_unwrap_or_exit_returns_the_ok_value():
    assert unwrap_or_exit(Ok(42), Console()) == 42


def test_unwrap_or_exit_json_prints_ok_false_and_error_on_err(capsys):
    with pytest.raises(typer.Exit) as exc_info:
        unwrap_or_exit(Err(ValueError("boom")), Console(), json_output=True)

    assert exc_info.value.exit_code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"ok": False, "error": "boom"}


def test_unwrap_or_exit_json_includes_the_prefix_in_the_error(capsys):
    with pytest.raises(typer.Exit):
        unwrap_or_exit(
            Err(ValueError("boom")), Console(), prefix="Sync failed: ", json_output=True
        )

    printed = json.loads(capsys.readouterr().out)
    assert printed == {"ok": False, "error": "Sync failed: boom"}


def test_unwrap_or_exit_non_json_prints_no_json_on_err(capsys):
    with pytest.raises(typer.Exit):
        unwrap_or_exit(Err(ValueError("boom")), Console(), json_output=False)

    out = capsys.readouterr().out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_emit_success_json_prints_ok_true_with_payload(capsys):
    human_called = []

    emit_success(True, {"name": "myproj"}, lambda: human_called.append(True))

    printed = json.loads(capsys.readouterr().out)
    assert printed == {"ok": True, "name": "myproj"}
    assert human_called == []


def test_emit_success_non_json_calls_human_and_prints_no_json(capsys):
    human_called = []

    emit_success(False, {"name": "myproj"}, lambda: human_called.append(True))

    out = capsys.readouterr().out
    assert human_called == [True]
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_with_status_json_runs_fn_with_no_spinner_and_passes_none(monkeypatch):
    console = Console()
    status_calls = []
    monkeypatch.setattr(console, "status", lambda *a, **k: status_calls.append(True))

    result = with_status(True, console, "Doing thing...", lambda status: status)

    assert result is None  # fn(None) - json mode never enters a status context
    assert status_calls == []  # console.status() itself was never called


def test_with_status_non_json_enters_a_spinner_and_passes_it_to_fn(monkeypatch):
    console = Console()

    class FakeStatus:
        def __init__(self):
            self.entered = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *exc_info):
            return False

    fake_status = FakeStatus()
    captured = {}

    def fake_status_factory(message, **kwargs):
        captured["message"] = message
        return fake_status

    monkeypatch.setattr(console, "status", fake_status_factory)

    result = with_status(False, console, "Doing thing...", lambda status: status)

    assert result is fake_status
    assert fake_status.entered is True
    assert captured["message"] == "Doing thing..."


def test_with_status_returns_fns_return_value():
    console = Console()

    assert with_status(True, console, "msg", lambda status: 42) == 42
