from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()


def test_cli_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_up_invokes_devpod_up(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert calls == [["devpod", "up", "my-project"]]


def test_ssh_forwards_extra_args(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = runner.invoke(cli_module.app, ["ssh", "my-project", "--", "ls", "-la"])

    assert result.exit_code == 0
    assert calls == [["devpod", "ssh", "my-project", "ls", "-la"]]


def test_stop_and_delete_invoke_devpod(monkeypatch):
    import devtemplate.cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(args):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    runner.invoke(cli_module.app, ["stop", "my-project"])
    runner.invoke(cli_module.app, ["delete", "my-project"])

    assert calls == [["devpod", "stop", "my-project"], ["devpod", "delete", "my-project"]]


def test_template_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["template", "--help"])
    assert result.exit_code == 0


def test_project_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["project", "--help"])
    assert result.exit_code == 0


def test_devpod_launch_failure_reports_clean_error(monkeypatch):
    import devtemplate.cli as cli_module

    def fake_run(args):
        raise FileNotFoundError("devpod not found")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 1
    assert "devpod" in result.stdout
