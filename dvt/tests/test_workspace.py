from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from logerr import Ok

from devtemplate import workspace as workspace_module
from devtemplate.container import compute_labels
from devtemplate.runtime import RuntimeHandle
from devtemplate.workspace import up_workspace

PROJECT_CONFIG = {
    "name": "fastapi",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
    "postCreateCommand": "pixi install",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(PROJECT_CONFIG))
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
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    build_calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: build_calls.append(k) or Ok("dvt/fastapi:latest"),
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module,
        "run_container",
        lambda *a, **k: Ok(fake_container),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_lifecycle_commands",
        lambda *a, **k: Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )
    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert result.unwrap() is fake_container
    assert build_calls == [{"nocache": False, "pull": False}]


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
    existing.labels = compute_labels(
        PROJECT_CONFIG,
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )
    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_called_once()


def test_up_workspace_noop_when_already_running(project, handle, settings, monkeypatch):
    existing = MagicMock()
    existing.status = "running"
    existing.labels = compute_labels(
        PROJECT_CONFIG,
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )
    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    existing.start.assert_not_called()


def test_up_workspace_resumes_existing_even_without_devcontainer_json(
    tmp_path, handle, settings, monkeypatch
):
    """devcontainer.json state must never gate resumability. up_workspace now
    reads it on the resume path too (to check for drift), but a missing file
    must not block resuming - the drift check is simply skipped, and the
    existing container is resumed exactly as if devcontainer.json were
    present and unchanged. Here there is no .devcontainer/devcontainer.json
    at all."""
    existing = MagicMock()
    existing.status = "exited"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", tmp_path)

    assert result.is_ok()
    assert result.unwrap() is existing
    existing.start.assert_called_once()


def test_up_workspace_ensures_gpu_support_on_podman_windows(
    project, settings, monkeypatch
):
    handle = RuntimeHandle(
        client=MagicMock(),
        engine="podman",
        cli_binary="/usr/bin/podman",
        machine_name="devpod-machine",
    )
    (project / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "jax",
                "image": "ghcr.io/jesserobertson/base-cuda:latest",
                "runArgs": ["--gpus", "all"],
            }
        )
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    calls = []
    monkeypatch.setattr(
        workspace_module.podman_machine,
        "ensure_gpu_support",
        lambda cli_binary, machine_name: calls.append(machine_name) or Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: Ok("dvt/jax:latest"),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_container",
        lambda *a, **k: Ok(MagicMock()),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_lifecycle_commands",
        lambda *a, **k: Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )

    result = up_workspace(handle, settings, "jax", project)

    assert result.is_ok()
    assert calls == ["devpod-machine"]


def test_up_workspace_skips_gpu_check_on_docker(project, settings, monkeypatch):
    handle = RuntimeHandle(
        client=MagicMock(), engine="docker", cli_binary="/usr/bin/docker"
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/x")),
    )
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: Ok("dvt/fastapi:latest"),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_container",
        lambda *a, **k: Ok(MagicMock()),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_lifecycle_commands",
        lambda *a, **k: Ok(None),
    )
    ensure_gpu_calls = []
    monkeypatch.setattr(
        workspace_module.podman_machine,
        "ensure_gpu_support",
        lambda *a, **k: ensure_gpu_calls.append(1) or Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert ensure_gpu_calls == []


def test_up_workspace_skips_gpu_check_on_podman_windows_without_gpus_arg(
    project, settings, monkeypatch
):
    """Guard requires BOTH machine_name set AND --gpus in runArgs.
    This test verifies that when machine_name is set but --gpus is absent
    from runArgs, ensure_gpu_support is not called."""
    handle = RuntimeHandle(
        client=MagicMock(),
        engine="podman",
        cli_binary="/usr/bin/podman",
        machine_name="devpod-machine",
    )
    (project / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "python",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "runArgs": ["--network=host"],
            }
        )
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/x")),
    )
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: Ok("dvt/python:latest"),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_container",
        lambda *a, **k: Ok(MagicMock()),
    )
    monkeypatch.setattr(
        workspace_module,
        "run_lifecycle_commands",
        lambda *a, **k: Ok(None),
    )
    ensure_gpu_calls = []
    monkeypatch.setattr(
        workspace_module.podman_machine,
        "ensure_gpu_support",
        lambda *a, **k: ensure_gpu_calls.append(1) or Ok(None),
    )
    monkeypatch.setattr(
        workspace_module,
        "write_ssh_config_entry",
        lambda *a, **k: Ok(None),
    )

    result = up_workspace(handle, settings, "python", project)

    assert result.is_ok()
    assert ensure_gpu_calls == []


def test_up_workspace_refuses_when_config_drifted(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.status = "running"
    existing.labels = compute_labels(
        PROJECT_CONFIG,
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    (project / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps({**PROJECT_CONFIG, "postCreateCommand": "pixi install --locked"})
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_err()
    assert "postCreateCommand" in str(result.unwrap_err())
    existing.remove.assert_not_called()


def test_up_workspace_refuses_when_stored_config_unreadable(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.labels = {}
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_err()
    assert "couldn't verify" in str(result.unwrap_err())
    existing.remove.assert_not_called()


def test_up_workspace_rebuild_tears_down_and_rebuilds(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.labels = compute_labels(
        PROJECT_CONFIG,
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    build_calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: build_calls.append(k) or Ok("dvt/fastapi:latest"),
    )
    fake_new_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_new_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    assert result.unwrap() is fake_new_container
    existing.remove.assert_called_once_with(force=True)
    handle.client.images.remove.assert_called_once_with(
        "dvt/fastapi:latest", force=True
    )
    assert build_calls == [{"nocache": True, "pull": True}]


def test_up_workspace_rebuild_skips_teardown_when_no_existing_container(
    project, handle, settings, monkeypatch
):
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: None
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: Ok("dvt/fastapi:latest")
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    assert result.unwrap() is fake_container
    handle.client.images.remove.assert_not_called()


def test_up_workspace_rebuild_proceeds_when_image_removal_fails(
    project, handle, settings, monkeypatch
):
    existing = MagicMock()
    existing.labels = compute_labels(
        PROJECT_CONFIG,
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    handle.client.images.remove.side_effect = RuntimeError("image in use")
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module,
        "pull_feature",
        lambda client, ref, cache_dir: Ok(Path("/extracted")),
    )
    monkeypatch.setattr(
        workspace_module, "build_image", lambda *a, **k: Ok("dvt/fastapi:latest")
    )
    fake_container = MagicMock()
    monkeypatch.setattr(
        workspace_module, "run_container", lambda *a, **k: Ok(fake_container)
    )
    monkeypatch.setattr(
        workspace_module, "run_lifecycle_commands", lambda *a, **k: Ok(None)
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_ok()
    existing.remove.assert_called_once_with(force=True)


def test_up_workspace_rebuild_validates_config_before_teardown(
    tmp_path, handle, settings, monkeypatch
):
    """--rebuild against an existing container must not tear anything down
    until the config it needs has been loaded and validated. Here
    devcontainer.json doesn't even exist, so the whole thing should fail
    before existing.remove() is ever called - leaving the workspace intact
    for the user to fix and retry."""
    existing = MagicMock()
    existing.labels = {"devcontainer.local_folder": str(tmp_path.resolve())}
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )

    result = up_workspace(handle, settings, "fastapi", tmp_path, rebuild=True)

    assert result.is_err()
    existing.remove.assert_not_called()


def test_up_workspace_rebuild_refuses_when_folder_does_not_match(
    project, handle, settings, monkeypatch
):
    """--rebuild against a container that was actually built from a
    different folder must refuse outright rather than tearing down the real
    workspace and rebuilding it with this folder's (unrelated) config."""
    existing = MagicMock()
    existing.labels = {"devcontainer.local_folder": "/some/other/project"}
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    build_calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: build_calls.append(1) or Ok("dvt/fastapi:latest"),
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_err()
    message = str(result.unwrap_err())
    assert "/some/other/project" in message
    assert str(project.resolve()) in message
    existing.remove.assert_not_called()
    assert build_calls == []


def test_up_workspace_rebuild_refuses_when_folder_label_is_missing(
    project, handle, settings, monkeypatch
):
    """--rebuild against a container with no devcontainer.local_folder label
    at all must refuse just like an explicit mismatch would - there's no way
    to confirm the container actually belongs to project_path, and tearing
    it down on an unconfirmed assumption is exactly the hazard this check
    exists to close. This is stricter than the not-rebuild path, where a
    missing label is harmless to fall through on since nothing gets
    destroyed there."""
    existing = MagicMock()
    existing.labels = {}
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    build_calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_image",
        lambda *a, **k: build_calls.append(1) or Ok("dvt/fastapi:latest"),
    )

    result = up_workspace(handle, settings, "fastapi", project, rebuild=True)

    assert result.is_err()
    existing.remove.assert_not_called()
    assert build_calls == []


def test_up_workspace_resumes_without_drift_check_when_folder_does_not_match(
    project, handle, settings, monkeypatch
):
    """Without --rebuild, a container whose devcontainer.local_folder doesn't
    match project_path must be resumed unconditionally - the drift check is
    skipped entirely, even when the stored config would otherwise look
    drifted, since dvt can't meaningfully compare against the wrong folder's
    devcontainer.json."""
    existing = MagicMock()
    existing.status = "exited"
    existing.labels = compute_labels(
        {**PROJECT_CONFIG, "postCreateCommand": "totally different"},
        "fastapi",
        project,
        project / ".devcontainer" / "devcontainer.json",
    )
    existing.labels["devcontainer.local_folder"] = "/some/other/project"
    monkeypatch.setattr(
        workspace_module, "find_workspace_container", lambda client, name: existing
    )
    monkeypatch.setattr(
        workspace_module, "write_ssh_config_entry", lambda *a, **k: Ok(None)
    )

    result = up_workspace(handle, settings, "fastapi", project)

    assert result.is_ok()
    assert result.unwrap() is existing
    existing.start.assert_called_once()
