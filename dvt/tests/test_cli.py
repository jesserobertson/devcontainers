from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()


def test_cli_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
