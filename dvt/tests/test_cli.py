from types import SimpleNamespace

from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()


def _fake_handle():
    """A stand-in for runtime.RuntimeHandle: cli.py reads .client and
    .cli_binary off whatever get_client() returns, so a bare object() isn't
    enough once those attributes are accessed downstream of the mocked
    get_client."""
    return SimpleNamespace(client=object(), cli_binary="docker")


def test_cli_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0


def test_up_builds_and_runs_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    fake_handle = object()
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path: cli_module.Ok(object()),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0


def test_up_reports_clean_error_on_failure(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(object())
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path: cli_module.Err(
            FileNotFoundError("no devcontainer.json")
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 1
    assert "devcontainer.json" in result.output


def test_ssh_interactive_execs_into_container(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module,
        "exec_interactive",
        lambda cli_binary, client, name: cli_module.Ok(0),
    )

    result = runner.invoke(cli_module.app, ["ssh", "my-project"])

    assert result.exit_code == 0


def test_ssh_stdio_uses_stdio_proxy(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "stdio_proxy",
        # dict.setdefault(k, True) returns True itself, which would short-circuit
        # `or` and make this lambda return the bare bool instead of Ok(0) - use
        # update() (which returns None) so the Result is what's actually returned.
        lambda cli_binary, client, name: (
            captured.update(called=True) or cli_module.Ok(0)
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh", "--stdio", "my-project"])

    assert result.exit_code == 0
    assert captured.get("called") is True


def test_ssh_reports_clean_error_on_exec_failure(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module,
        "exec_interactive",
        lambda cli_binary, client, name: cli_module.Err(
            RuntimeError("docker exec failed")
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh", "my-project"])

    assert result.exit_code == 1


def test_stop_stops_the_labeled_container(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"stop": lambda self: None})()
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 0


def test_stop_reports_clean_error_when_not_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: None
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 1


def test_delete_removes_container_and_ssh_entry(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(
        cli_module, "get_client", lambda runtime: cli_module.Ok(_fake_handle())
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete", "my-project"])

    assert result.exit_code == 0


def test_template_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["template", "--help"])
    assert result.exit_code == 0


def test_project_subcommand_is_registered():
    from devtemplate.cli import app

    result = runner.invoke(app, ["project", "--help"])
    assert result.exit_code == 0
