import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app
from devtemplate.describe import describe_app

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


def test_sync_reports_synced_features_and_images(settings, monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    # No cached templates and cwd outside any devcontainers checkout, so the
    # feature pre-pull step has nothing to fetch (keeps this test offline).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["fastapi", "agent"]),
    )
    monkeypatch.setattr(
        cli_module,
        "sync_images",
        lambda settings_arg, client: cli_module.Ok(["base-cuda"]),
    )

    result = runner.invoke(cli_module.app, ["sync"])

    assert result.exit_code == 0, result.output
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
    assert "base-cuda" in result.stdout


def test_sync_clears_the_pulled_feature_artifact_cache(settings, monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    stale = settings.features_dir / "deadbeef"
    stale.mkdir(parents=True)
    (stale / "install.sh").write_text("echo stale\n")

    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["fastapi"]),
    )
    monkeypatch.setattr(
        cli_module, "sync_images", lambda settings_arg, client: cli_module.Ok([])
    )

    result = runner.invoke(cli_module.app, ["sync"])

    assert result.exit_code == 0
    assert not stale.exists()


def test_sync_json_prints_ok_true_with_synced_names(settings, monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["fastapi"]),
    )
    monkeypatch.setattr(
        cli_module,
        "sync_images",
        lambda settings_arg, client: cli_module.Ok(["base-cuda"]),
    )

    result = runner.invoke(cli_module.app, ["sync", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed == {
        "ok": True,
        "features": ["fastapi"],
        "images": ["base-cuda"],
        "feature_specs": [],
    }
    _assert_matches_declared_output_schema("sync", printed)


def test_sync_json_prints_ok_false_on_feature_sync_failure(settings, monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Err(
            RuntimeError("network unreachable")
        ),
    )
    monkeypatch.setattr(
        cli_module, "sync_images", lambda settings_arg, client: cli_module.Ok([])
    )

    result = runner.invoke(cli_module.app, ["sync", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "network unreachable" in printed["error"]


def test_sync_json_prints_ok_false_on_image_sync_failure(settings, monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["fastapi"]),
    )
    monkeypatch.setattr(
        cli_module,
        "sync_images",
        lambda settings_arg, client: cli_module.Err(RuntimeError("image sync broke")),
    )

    result = runner.invoke(cli_module.app, ["sync", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "image sync broke" in printed["error"]


def _write_cached_template(settings, name, feature_refs):
    """Write a minimal cached template devcontainer.json whose `features`
    map keys are `feature_refs`, mirroring what `dvt sync` leaves in the
    templates cache."""
    directory = settings.templates_dir / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "devcontainer.json").write_text(
        json.dumps({"features": {ref: {} for ref in feature_refs}})
    )


def _fake_pull_feature(fail_ids=()):
    """Stand-in for `features.pull_feature`: instead of hitting an OCI
    registry it drops a `devcontainer-feature.json` (just an `id`) into the
    cache dir so the real `resolve_feature_graph` / `load_cached_specs` can
    read it back. Any ref whose short id is in `fail_ids` returns an Err."""
    import devtemplate.cli as cli_module
    from devtemplate.feature_graph import ref_to_id

    def _pull(client, ref, cache_dir):
        ident = ref_to_id(ref)
        if ident in fail_ids:
            return cli_module.Err(RuntimeError(f"cannot pull {ident}"))
        extracted = Path(cache_dir) / ident
        extracted.mkdir(parents=True, exist_ok=True)
        (extracted / "devcontainer-feature.json").write_text(json.dumps({"id": ident}))
        return cli_module.Ok(extracted)

    return _pull


def test_sync_json_lists_feature_specs_for_cached_templates(
    settings, monkeypatch, tmp_path
):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    _write_cached_template(settings, "agent", ["ghcr.io/acme/node:1"])
    _write_cached_template(settings, "fastapi", ["ghcr.io/acme/py-devtools:latest"])

    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["agent", "fastapi"]),
    )
    monkeypatch.setattr(
        cli_module, "sync_images", lambda settings_arg, client: cli_module.Ok([])
    )
    monkeypatch.setattr(cli_module, "pull_feature", _fake_pull_feature())

    result = runner.invoke(cli_module.app, ["sync", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed["ok"] is True
    assert printed["feature_specs"] == ["node", "py-devtools"]
    _assert_matches_declared_output_schema("sync", printed)


def test_sync_isolates_a_single_feature_pull_failure(settings, monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    monkeypatch.chdir(tmp_path)
    _write_cached_template(settings, "agent", ["ghcr.io/acme/node:1"])
    _write_cached_template(settings, "fastapi", ["ghcr.io/acme/py-devtools:latest"])

    monkeypatch.setattr(
        cli_module,
        "sync_templates",
        lambda settings_arg, client: cli_module.Ok(["agent", "fastapi"]),
    )
    monkeypatch.setattr(
        cli_module, "sync_images", lambda settings_arg, client: cli_module.Ok([])
    )
    monkeypatch.setattr(
        cli_module, "pull_feature", _fake_pull_feature(fail_ids={"node"})
    )

    result = runner.invoke(cli_module.app, ["sync", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.stdout)
    assert printed["ok"] is True
    assert printed["feature_specs"] == ["py-devtools"]
    assert "node" not in printed["feature_specs"]
    assert "could not pull feature" in result.stderr
    assert "ghcr.io/acme/node:1" in result.stderr


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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Ok(object())
        ),
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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Ok(object())
        ),
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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Ok(object())
        ),
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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Err(FileNotFoundError("no devcontainer.json"))
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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Err(FileNotFoundError("no devcontainer.json"))
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


def _stub_run_deps(monkeypatch, cli_module, exec_result):
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    captured = {}

    def fake_exec_command(cli_binary, client, name, command, *, tty):
        captured.update(name=name, command=command, tty=tty)
        return exec_result

    monkeypatch.setattr(cli_module, "exec_command", fake_exec_command)
    return captured


def test_run_execs_the_given_command_in_the_workspace(monkeypatch):
    import devtemplate.cli as cli_module

    captured = _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))

    result = runner.invoke(cli_module.app, ["run", "-n", "my-project", "pytest", "-q"])

    assert result.exit_code == 0
    assert captured == {"name": "my-project", "command": ["pytest", "-q"], "tty": False}


def test_run_propagates_the_child_exit_code(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(3))

    result = runner.invoke(cli_module.app, ["run", "-n", "my-project", "false"])

    assert result.exit_code == 3


def test_run_tty_flag_requests_a_terminal(monkeypatch):
    import devtemplate.cli as cli_module

    captured = _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))

    result = runner.invoke(
        cli_module.app, ["run", "-n", "my-project", "--tty", "python"]
    )

    assert result.exit_code == 0
    assert captured["tty"] is True


def test_run_reports_clean_error_when_exec_fails(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(
        monkeypatch, cli_module, cli_module.Err(RuntimeError("no such workspace"))
    )

    result = runner.invoke(cli_module.app, ["run", "-n", "my-project", "pytest"])

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


def test_stop_shows_a_status_spinner_while_stopping(monkeypatch):
    import devtemplate.cli as cli_module

    stop_calls = []
    fake_container = type("C", (), {"stop": lambda self: stop_calls.append(True)})()
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module, "find_workspace_container", lambda client, name: fake_container
    )

    entered = []

    class FakeStatus:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *exc_info):
            return False

    captured = {}

    def fake_status(message, **kwargs):
        captured["message"] = message
        return FakeStatus()

    monkeypatch.setattr(cli_module.console, "status", fake_status)

    result = runner.invoke(cli_module.app, ["stop", "my-project"])

    assert result.exit_code == 0
    assert entered == [True]
    assert stop_calls == [True]
    assert "my-project" in captured["message"]


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


def test_delete_shows_a_status_spinner_while_deleting(monkeypatch):
    import devtemplate.cli as cli_module

    remove_calls = []
    fake_container = type(
        "C", (), {"remove": lambda self, force=True: remove_calls.append(True)}
    )()
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

    entered = []

    class FakeStatus:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *exc_info):
            return False

    captured = {}

    def fake_status(message, **kwargs):
        captured["message"] = message
        return FakeStatus()

    monkeypatch.setattr(cli_module.console, "status", fake_status)

    result = runner.invoke(cli_module.app, ["delete", "my-project"])

    assert result.exit_code == 0
    assert entered == [True]
    assert remove_calls == [True]
    assert "my-project" in captured["message"]


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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            captured.update(name=name) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up"])

    assert result.exit_code == 0, result.output
    assert captured["name"] == "reused-name"
    assert "reused-name" in result.output


def test_up_drives_a_status_spinner_from_on_stage(monkeypatch, tmp_path):
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

    class FakeStatus:
        def __init__(self):
            self.updates: list[str] = []

        def update(self, text):
            self.updates.append(text)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    fake_status = FakeStatus()
    monkeypatch.setattr(cli_module.console, "status", lambda *a, **k: fake_status)

    captured = {}

    def fake_up_workspace(handle, settings, name, path, rebuild=False, on_stage=None):
        captured["on_stage"] = on_stage
        on_stage("Building image...")
        return cli_module.Ok(object())

    monkeypatch.setattr(cli_module, "up_workspace", fake_up_workspace)

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert captured["on_stage"] == fake_status.update
    assert fake_status.updates == ["Building image..."]


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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
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
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            captured.update(rebuild=rebuild) or cli_module.Ok(object())
        ),
    )

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert captured["rebuild"] is False


def _stub_up(monkeypatch, cli_module):
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "up_workspace",
        lambda handle, settings, name, path, rebuild=False, on_stage=None: (
            cli_module.Ok(object())
        ),
    )


def test_no_verbose_or_debug_leaves_logerr_untouched(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)
    _stub_up(monkeypatch, cli_module)

    configure_calls = []
    monkeypatch.setattr(
        cli_module.logerr, "configure", lambda **kw: configure_calls.append(kw)
    )
    add_calls = []
    monkeypatch.setattr(cli_module.logger, "add", lambda *a, **kw: add_calls.append(kw))

    result = runner.invoke(cli_module.app, ["up", "my-project"])

    assert result.exit_code == 0
    assert configure_calls == []
    assert add_calls == []


def test_verbose_flag_enables_info_level_diagnostics(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)
    _stub_up(monkeypatch, cli_module)

    configure_calls = []
    monkeypatch.setattr(
        cli_module.logerr, "configure", lambda **kw: configure_calls.append(kw)
    )
    add_calls = []
    monkeypatch.setattr(cli_module.logger, "add", lambda *a, **kw: add_calls.append(kw))

    result = runner.invoke(cli_module.app, ["--verbose", "up", "my-project"])

    assert result.exit_code == 0
    assert configure_calls == [{"enabled": True, "level": "INFO"}]
    assert add_calls == [{"level": "INFO"}]


def test_debug_flag_enables_debug_level_diagnostics(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)
    _stub_up(monkeypatch, cli_module)

    configure_calls = []
    monkeypatch.setattr(
        cli_module.logerr, "configure", lambda **kw: configure_calls.append(kw)
    )
    add_calls = []
    monkeypatch.setattr(cli_module.logger, "add", lambda *a, **kw: add_calls.append(kw))

    result = runner.invoke(cli_module.app, ["--debug", "up", "my-project"])

    assert result.exit_code == 0
    assert configure_calls == [{"enabled": True, "level": "DEBUG"}]
    assert add_calls == [{"level": "DEBUG"}]


def test_debug_takes_precedence_over_verbose(monkeypatch, tmp_path):
    import devtemplate.cli as cli_module

    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"name": "x", "image": "base:latest"}'
    )
    monkeypatch.chdir(tmp_path)
    _stub_up(monkeypatch, cli_module)

    configure_calls = []
    monkeypatch.setattr(
        cli_module.logerr, "configure", lambda **kw: configure_calls.append(kw)
    )
    add_calls = []
    monkeypatch.setattr(cli_module.logger, "add", lambda *a, **kw: add_calls.append(kw))

    result = runner.invoke(cli_module.app, ["--debug", "--verbose", "up", "my-project"])

    assert result.exit_code == 0
    assert configure_calls == [{"enabled": True, "level": "DEBUG"}]
    assert add_calls == [{"level": "DEBUG"}]


def test_info_is_registered_as_a_top_level_command():
    result = runner.invoke(app, ["info", "--help"])
    assert result.exit_code == 0


def _stub_forward_deps(monkeypatch, cli_module, *, build_result):
    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok(name or "inferred"),
    )
    monkeypatch.setattr(cli_module, "build_forwarder", lambda *a, **k: build_result)
    monkeypatch.setattr(cli_module, "block_forever", lambda: None)


def test_forward_prints_mappings_and_tears_down(monkeypatch):
    import devtemplate.cli as cli_module

    closed = {"n": 0}
    fake_fwd = SimpleNamespace(
        summary_lines=lambda: ["127.0.0.1:2718 -> web:localhost:2718"],
        close=lambda: closed.__setitem__("n", closed["n"] + 1),
    )
    _stub_forward_deps(monkeypatch, cli_module, build_result=cli_module.Ok(fake_fwd))

    result = runner.invoke(cli_module.app, ["forward", "-n", "web", "2718"])

    assert result.exit_code == 0, result.output
    assert "127.0.0.1:2718 -> web:localhost:2718" in result.output
    assert "Stopped forwarding." in result.output
    assert closed["n"] == 1


def test_forward_reports_setup_failure(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_forward_deps(
        monkeypatch,
        cli_module,
        build_result=cli_module.Err(ValueError("local port 2718 is unavailable")),
    )

    result = runner.invoke(cli_module.app, ["forward", "-n", "web", "2718"])

    assert result.exit_code == 1
    assert "2718" in result.output


def test_forward_requires_at_least_one_spec(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_forward_deps(monkeypatch, cli_module, build_result=cli_module.Ok(None))
    result = runner.invoke(cli_module.app, ["forward", "-n", "web"])
    assert result.exit_code != 0


def test_run_with_dash_L_builds_and_closes_a_forwarder(monkeypatch):
    import devtemplate.cli as cli_module

    captured = _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))
    events: list[str] = []
    fake_fwd = SimpleNamespace(
        summary_lines=lambda: [], close=lambda: events.append("closed")
    )

    def fake_build(client, cli_binary, name, specs):
        events.append(f"built:{specs}")
        return cli_module.Ok(fake_fwd)

    monkeypatch.setattr(cli_module, "build_forwarder", fake_build)

    result = runner.invoke(
        cli_module.app,
        ["run", "-n", "web", "-L", "2718", "python", "-m", "http.server"],
    )

    assert result.exit_code == 0, result.output
    assert events == ["built:['2718']", "closed"]
    assert captured["command"] == ["python", "-m", "http.server"]


def test_run_does_not_steal_the_commands_own_dash_L(monkeypatch):
    import devtemplate.cli as cli_module

    captured = _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))
    # build_forwarder must NOT be called - no -L before the command
    monkeypatch.setattr(
        cli_module,
        "build_forwarder",
        lambda *a, **k: pytest.fail(
            "build_forwarder called; -L was stolen from the command"
        ),
    )
    result = runner.invoke(
        cli_module.app, ["run", "-n", "web", "curl", "-L", "http://example"]
    )
    assert result.exit_code == 0, result.output
    assert captured["command"] == ["curl", "-L", "http://example"]


def test_run_forwarder_closed_even_when_command_fails(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(7))
    closed = {"n": 0}
    monkeypatch.setattr(
        cli_module,
        "build_forwarder",
        lambda *a, **k: cli_module.Ok(
            SimpleNamespace(
                summary_lines=lambda: [],
                close=lambda: closed.__setitem__("n", closed["n"] + 1),
            )
        ),
    )

    result = runner.invoke(cli_module.app, ["run", "-n", "web", "-L", "2718", "false"])

    assert result.exit_code == 7
    assert closed["n"] == 1


def test_run_dash_L_build_failure_exits_one(monkeypatch):
    import devtemplate.cli as cli_module

    _stub_run_deps(monkeypatch, cli_module, cli_module.Ok(0))
    monkeypatch.setattr(
        cli_module,
        "build_forwarder",
        lambda *a, **k: cli_module.Err(ValueError("port 2718 unavailable")),
    )
    result = runner.invoke(cli_module.app, ["run", "-n", "web", "-L", "2718", "true"])
    assert result.exit_code == 1
    assert "2718" in result.output


def test_ssh_with_dash_L_builds_and_closes_a_forwarder(monkeypatch):
    import devtemplate.cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "get_client",
        lambda runtime, **kwargs: cli_module.Ok(_fake_handle()),
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_existing",
        lambda client, name, cwd, command: cli_module.Ok(name),
    )
    monkeypatch.setattr(
        cli_module,
        "exec_interactive",
        lambda cli_binary, client, name: cli_module.Ok(0),
    )
    closed = {"n": 0}
    monkeypatch.setattr(
        cli_module,
        "build_forwarder",
        lambda *a, **k: cli_module.Ok(
            SimpleNamespace(
                summary_lines=lambda: [],
                close=lambda: closed.__setitem__("n", closed["n"] + 1),
            )
        ),
    )

    result = runner.invoke(cli_module.app, ["ssh", "web", "-L", "2718"])

    assert result.exit_code == 0, result.output
    assert closed["n"] == 1
