from __future__ import annotations

import json
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from devtemplate.commands.info import info

app = typer.Typer()
app.command("info")(info)


@app.command("noop")
def _noop() -> None:
    pass


runner = CliRunner()


def _write_devcontainer_json(tmp_path, config: dict) -> None:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir(exist_ok=True)
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(config))


def test_info_refuses_when_devcontainer_json_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 1
    assert "dvt init" in result.output


def test_info_shows_untracked_features_when_no_sidecar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path,
        {
            "name": "my-project",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        },
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "my-project" in result.output
    assert "ghcr.io/jesserobertson/base-ubuntu:latest" in result.output
    assert "ghcr.io/jesserobertson/devcontainers/fastapi:latest" in result.output
    assert "untracked" in result.output


def test_info_shows_tracked_feature_names_from_sidecar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    (tmp_path / ".devcontainer" / "dvt-features.json").write_text(
        json.dumps(
            {
                "init": {},
                "applied": [
                    {"name": "fastapi", "overlay": {}},
                    {"name": "agent", "overlay": {}},
                ],
            }
        )
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "fastapi" in result.output
    assert "agent" in result.output
    assert "untracked" not in result.output


def test_info_notes_when_no_runtime_reachable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    monkeypatch.setattr(
        info_module,
        "get_client",
        lambda *args, **kwargs: info_module.Err(RuntimeError("no runtime")),
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "no container runtime reachable" in result.output.lower()


def test_info_calls_get_client_without_podman_auto_start_or_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    captured = {}

    def fake_get_client(runtime, **kwargs):
        captured["kwargs"] = kwargs
        return info_module.Err(RuntimeError("no runtime"))

    monkeypatch.setattr(info_module, "get_client", fake_get_client)

    runner.invoke(app, ["info"])

    assert captured["kwargs"] == {
        "podman_machine_auto_init": False,
        "podman_machine_auto_start": False,
    }


def test_info_reports_no_workspace_running_when_zero_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module, "find_workspace_containers_by_folder", lambda client, folder: []
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "dvt up" in result.output


def test_info_shows_live_status_for_single_matching_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    fake_container = MagicMock(status="running", labels={"dvt.workspace": "my-project"})
    fake_container.name = "dvt-my-project"
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module,
        "find_workspace_containers_by_folder",
        lambda client, folder: [fake_container],
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "my-project" in result.output
    assert "running" in result.output
    assert "dvt-my-project" in result.output


def test_info_lists_all_matches_when_multiple_workspaces_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_devcontainer_json(
        tmp_path, {"name": "my-project", "image": "ghcr.io/x/base:latest"}
    )
    import devtemplate.commands.info as info_module

    fake_handle = MagicMock(client=MagicMock())
    fake_containers = [
        MagicMock(labels={"dvt.workspace": "bar"}),
        MagicMock(labels={"dvt.workspace": "foo"}),
    ]
    monkeypatch.setattr(
        info_module, "get_client", lambda *args, **kwargs: info_module.Ok(fake_handle)
    )
    monkeypatch.setattr(
        info_module,
        "find_workspace_containers_by_folder",
        lambda client, folder: fake_containers,
    )

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "bar" in result.output
    assert "foo" in result.output
