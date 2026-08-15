from __future__ import annotations

import json

import pytest
import typer
from logerr import Err, Ok
from rich.console import Console

from devtemplate.cli_support import emit_success, unwrap_or_exit


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
