from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devtemplate import workspace as workspace_module
from devtemplate.runtime import RuntimeHandle
from devtemplate.workspace import up_workspace


@pytest.fixture
def project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
                "postCreateCommand": "pixi install",
            }
        )
    )
    return tmp_path


@pytest.fixture
def handle() -> RuntimeHandle:
    return RuntimeHandle(
        client=MagicMock(), engine="docker", cli_binary="/usr/bin/docker"
    )


def test_up_workspace_errs_when_no_devcontainer_json(
    tmp_path, handle, settings, monkeypatch
):
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    result = up_workspace(handle, settings, "my-project", tmp_path)
    assert result.is_err()


def test_up_workspace_full_build_and_run_sequence(
    project, handle, settings, monkeypatch
):
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: workspace_module.Ok(Path("/extracted")),
    )
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: workspace_module.Ok("dvt/fastapi:latest"),
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module,
        "run_container",
        lambda *a, **k: workspace_module.Ok(fake_container),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_lifecycle_commands",
        lambda *a, **k: workspace_module.Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: workspace_module.Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert result.unwrap() is fake_container


def test_up_workspace_refuses_unsupported_config(
    tmp_path, handle, settings, monkeypatch
):
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"dockerComposeFile": "x.yml"})
    )

    result = up_workspace(handle, settings, "x", tmp_path)

    assert result.is_err()


def test_up_workspace_starts_existing_stopped_container(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.status = "exited"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: workspace_module.Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_called_once()


def test_up_workspace_noop_when_already_running(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "running"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: workspace_module.Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_not_called()


def test_up_workspace_resumes_existing_even_without_devcontainer_json(
    tmp_path, handle, settings, monkeypatch
):
    """devcontainer.json state must never gate resumability: the container
    label is the sole source of truth for whether a workspace exists. Here
    there is no .devcontainer/devcontainer.json at all (missing/invalid), yet
    an existing container is found, so up_workspace must resume it without
    ever loading or validating the config."""
    existing = MagicMock()
    existing.status = "exited"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: workspace_module.Ok(None),
    )

    def _fail_load_config(config_file):
        raise AssertionError("_load_config must not be called on the resume path")

    monkeypatch.setattr(workspace_module, "_load_config", _fail_load_config)

    result = up_workspace(handle, settings, "fastapi", tmp_path)

    assert result.is_ok()
    assert result.unwrap() is existing
    existing.start.assert_called_once()
