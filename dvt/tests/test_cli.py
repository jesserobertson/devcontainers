import json
from types import SimpleNamespace

import jsonschema
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app
from devtemplate.cli_support import describe_app

runner = CliRunner()


def _assert_matches_declared_output_schema(command_name: str, payload: dict) -> None:
    """Validate a command's real --json output against the exact schema
    dvt --describe publishes for it - not a hand-copied expectation, the
    live schema a real consumer would fetch. Proves the two can't have
    silently drifted apart."""
    schema = describe_app(app, version=__version__)["commands"][command_name]["output"][
        "success"
    ]
    jsonschema.validate(instance=payload, schema=schema)


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

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: cli_module.Ok(object()),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0


def test_up_passes_podman_machine_settings_to_get_client(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DVT_PODMAN_MACHINE_AUTO_INIT", "true")
    monkeypatch.setenv("DVT_PODMAN_MACHINE_AUTO_START", "false")

    captured = {}

    def fake_get_client(runtime, **kwargs):
        captured["runtime"] = runtime
        captured["kwargs"] = kwargs
        return cli_module.Ok(_fake_handle())

    monkeypatch.setattr(cli_module, "get_client", fake_get_client)
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: cli_module.Ok(object()),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert captured["kwargs"] == {
        "podman_machine_auto_init": True,
        "podman_machine_auto_start": False,
    }


def test_up_json_prints_ok_true_with_name_on_success(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: cli_module.Ok(object()),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "name": "my-project"}
    _assert_matches_declared_output_schema("up", printed)


def test_up_json_prints_ok_false_with_error_on_failure(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: cli_module.Err(
            FileNotFoundError("no devcontainer.json")
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "devcontainer.json" in printed["error"]


def test_up_reports_clean_error_on_failure(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: cli_module.Err(
            FileNotFoundError("no devcontainer.json")
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 1
    assert "devcontainer.json" in result.output


def test_ssh_interactive_execs_into_container(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
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
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
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
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
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
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 0


def test_stop_json_prints_ok_true_with_name_on_success(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"stop": lambda self: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "name": "my-project"}
    _assert_matches_declared_output_schema("stop", printed)


def test_stop_json_prints_ok_false_when_not_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: None
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False


def test_stop_reports_clean_error_when_not_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: None
    )

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 1


def test_stop_reports_clean_error_when_lookup_raises(monkeypatch):
    import devtemplate.cli as cli_module

    def _raise(client, name):
        raise RuntimeError("daemon unreachable")

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(cli_module, "find_workspace_container", _raise)

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 1
    assert "daemon unreachable" in result.output


def test_delete_removes_container_and_ssh_entry(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete", "my-project"])

    assert result.exit_code == 0


def test_delete_json_prints_ok_true_with_name_on_success(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete", "my-project", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "name": "my-project"}
    _assert_matches_declared_output_schema("delete", printed)


def test_up_infers_name_from_the_single_matching_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_for_up",
        lambda client, name, cwd: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: (
            captured.update(name=name) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up"])

    assert result.exit_code == 0, result.output
    assert captured["name"] == "reused-name"
    assert "reused-name" in result.output


def test_up_reports_clean_error_when_multiple_workspaces_match(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_for_up",
        lambda client, name, cwd: cli_module.Err(
            ValueError("Multiple workspaces match this folder: bar, foo.")
        ),
    )

    result = runner.invoke(cli_module.app, ["up"])

    assert result.exit_code == 1
    assert "bar" in result.output
    assert "foo" in result.output


def test_ssh_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "exec_interactive",
        lambda cli_binary, client, name: captured.update(name=name) or cli_module.Ok(0),
    )

    result = runner.invoke(cli_module.app, ["ssh"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_ssh_reports_clean_error_when_no_workspace_found(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Err(
            ValueError("No workspace found for this folder.")
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh"])

    assert result.exit_code == 1
    assert "No workspace found" in result.output


def test_stop_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"stop": lambda self: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "find_workspace_container",
        lambda client, name: captured.update(name=name) or fake_container,
    )

    result = runner.invoke(cli_module.app, ["stop"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_delete_infers_name_from_the_single_matching_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    fake_container = type("C", (), {"remove": lambda self, force=True: None})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok("reused-name"),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "find_workspace_container",
        lambda client, name: captured.update(name=name) or fake_container,
    )
    monkeypatch.setattr(
        cli_module, "remove_ssh_config_entry", lambda name, path: cli_module.Ok(None)
    )

    result = runner.invoke(cli_module.app, ["delete"])

    assert result.exit_code == 0
    assert captured["name"] == "reused-name"


def test_up_rebuild_flag_threads_through_to_up_workspace(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: (
            captured.update(rebuild=rebuild) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project", "--rebuild"])

    assert result.exit_code == 0
    assert captured["rebuild"] is True


def test_up_without_rebuild_flag_threads_false_through_to_up_workspace(
    monkeypatch, tmp_path
):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    captured = {}
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False: (
            captured.update(rebuild=rebuild) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert captured["rebuild"] is False


def test_info_is_registered_as_a_top_level_command():
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code == 0
