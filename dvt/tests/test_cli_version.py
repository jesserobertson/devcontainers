from __future__ import annotations

from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_flag_works_without_settings_or_runtime(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("settings/runtime should not be touched by --version")

    monkeypatch.setattr("devtemplate.cli.load_settings", _boom)

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
