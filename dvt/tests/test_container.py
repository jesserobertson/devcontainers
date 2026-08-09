from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from devtemplate.container import (
    compute_labels,
    find_workspace_container,
    refuse_unsupported,
    resolve_workspace,
    run_container,
    run_lifecycle_commands,
)

FASTAPI_CONFIG = {
    "name": "fastapi",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "workspaceFolder": "/workspace",
    "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
    "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
    "mounts": ["source=fastapi-pixi-cache,target=/home/dev/.cache/pixi,type=volume"],
    "postCreateCommand": "pixi install",
    "remoteUser": "dev",
}

AGENT_CONFIG = {
    "name": "agent",
    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
    "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
    "postCreateCommand": "pixi install",
    "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
    "remoteUser": "dev",
}


@pytest.mark.parametrize("config", [FASTAPI_CONFIG, AGENT_CONFIG])
def test_refuse_unsupported_allows_current_templates(config):
    assert refuse_unsupported(config).is_ok()


def test_refuse_unsupported_rejects_compose():
    result = refuse_unsupported({"dockerComposeFile": "docker-compose.yml"})
    assert result.is_err()


def test_refuse_unsupported_rejects_build_dockerfile():
    result = refuse_unsupported({"build": {"dockerfile": "Dockerfile"}})
    assert result.is_err()


@pytest.mark.parametrize(
    "field",
    [
        "onCreateCommand",
        "updateContentCommand",
        "initializeCommand",
        "postAttachCommand",
    ],
)
def test_refuse_unsupported_rejects_unsupported_lifecycle_fields(field):
    result = refuse_unsupported({field: "echo hi"})
    assert result.is_err()


def test_refuse_unsupported_rejects_installs_after():
    config = {
        "features": {"ghcr.io/x/y:latest": {"installsAfter": ["ghcr.io/x/z:latest"]}}
    }
    result = refuse_unsupported(config)
    assert result.is_err()


def test_resolve_workspace_uses_explicit_fields(tmp_path):
    folder, mount = resolve_workspace(FASTAPI_CONFIG, tmp_path)
    assert folder == "/workspace"
    assert "target=/workspace" in mount


def test_resolve_workspace_defaults_when_absent(tmp_path):
    project = tmp_path / "my-project"
    project.mkdir()
    folder, mount = resolve_workspace({}, project)
    assert folder == "/workspaces/my-project"
    assert f"target={folder}" in mount
    assert "type=bind" in mount


def test_run_container_substitutes_local_workspace_folder_variable(tmp_path):
    """Templates (e.g. this repo's own cli/fastapi/agent templates) write
    workspaceMount with the devcontainer.json standard ${localWorkspaceFolder}
    variable rather than a literal path. Left unsubstituted, Docker/Podman
    can't tell the mount source is meant to be a host path, so it tries to
    create a named volume literally called '${localWorkspaceFolder}' - an
    invalid volume name - and container creation fails."""
    config = {
        **FASTAPI_CONFIG,
        "workspaceMount": (
            "source=${localWorkspaceFolder},target=/workspace,"
            "type=bind,consistency=cached"
        ),
    }
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    result = run_container(
        fake_client,
        "dvt/fastapi:latest",
        config,
        "fastapi",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert str(tmp_path.resolve()) in kwargs["volumes"]
    assert "${localWorkspaceFolder}" not in kwargs["volumes"]


def test_compute_labels_encodes_metadata(tmp_path):
    config_file = tmp_path / ".devcontainer" / "devcontainer.json"
    labels = compute_labels(FASTAPI_CONFIG, "my-project", tmp_path, config_file)
    assert labels["dvt.workspace"] == "my-project"
    assert labels["devcontainer.local_folder"] == str(tmp_path.resolve())
    assert labels["devcontainer.config_file"] == str(config_file.resolve())
    assert "devcontainer.metadata" in labels


def test_run_container_translates_cap_add(tmp_path):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_client.containers.run.return_value = fake_container

    result = run_container(
        fake_client,
        "dvt/agent:latest",
        AGENT_CONFIG,
        "agent",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert set(kwargs["cap_add"]) == {"NET_ADMIN", "NET_RAW"}


def test_run_container_overrides_entrypoint_to_keep_container_alive(tmp_path):
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    result = run_container(
        fake_client,
        "dvt/agent:latest",
        AGENT_CONFIG,
        "agent",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert kwargs["entrypoint"] == ["sleep", "infinity"]


def test_run_container_respects_override_command_false(tmp_path):
    config = {**AGENT_CONFIG, "overrideCommand": False}
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    result = run_container(
        fake_client,
        "dvt/agent:latest",
        config,
        "agent",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert kwargs["entrypoint"] is None


def test_run_container_translates_gpus_all(tmp_path):
    config = {**FASTAPI_CONFIG, "runArgs": ["--gpus", "all"]}
    fake_client = MagicMock()
    fake_client.containers.run.return_value = MagicMock()

    result = run_container(
        fake_client,
        "dvt/jax:latest",
        config,
        "jax",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_ok()
    _, kwargs = fake_client.containers.run.call_args
    assert len(kwargs["device_requests"]) == 1


def test_run_container_rejects_unknown_run_arg(tmp_path):
    config = {**FASTAPI_CONFIG, "runArgs": ["--privileged"]}
    fake_client = MagicMock()

    result = run_container(
        fake_client,
        "dvt/x:latest",
        config,
        "x",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_err()


def test_run_container_returns_err_when_docker_client_raises(tmp_path):
    fake_client = MagicMock()
    fake_client.containers.run.side_effect = RuntimeError("daemon unreachable")

    result = run_container(
        fake_client,
        "dvt/x:latest",
        FASTAPI_CONFIG,
        "x",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_err()
    assert isinstance(result.unwrap_err(), RuntimeError)


def test_run_container_returns_err_on_malformed_mount_spec(tmp_path):
    config = {**FASTAPI_CONFIG, "mounts": ["type=volume,no-source-or-target-here"]}
    fake_client = MagicMock()

    result = run_container(
        fake_client,
        "dvt/x:latest",
        config,
        "x",
        tmp_path,
        tmp_path / "devcontainer.json",
    )

    assert result.is_err()


def test_run_lifecycle_commands_runs_in_order():
    calls = []
    fake_container = MagicMock()

    def fake_exec_run(cmd):
        calls.append(cmd[-1])
        return (0, b"ok")

    fake_container.exec_run.side_effect = fake_exec_run

    result = run_lifecycle_commands(fake_container, AGENT_CONFIG)

    assert result.is_ok()
    assert calls == ["pixi install", "sudo /usr/local/bin/init-firewall.sh"]


def test_run_lifecycle_commands_stops_on_failure():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (1, b"boom")

    result = run_lifecycle_commands(fake_container, FASTAPI_CONFIG)

    assert result.is_err()


def test_run_lifecycle_commands_returns_err_when_exec_run_raises():
    fake_container = MagicMock()
    fake_container.exec_run.side_effect = RuntimeError("container is not running")

    result = run_lifecycle_commands(fake_container, FASTAPI_CONFIG)

    assert result.is_err()
    assert isinstance(result.unwrap_err(), RuntimeError)


def test_run_lifecycle_commands_decodes_streamed_output_on_failure():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (1, iter([b"boo", b"m"]))

    result = run_lifecycle_commands(fake_container, FASTAPI_CONFIG)

    assert result.is_err()
    assert "boom" in str(result.unwrap_err())


def test_find_workspace_container_filters_by_label():
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_client.containers.list.return_value = [fake_container]

    found = find_workspace_container(fake_client, "my-project")

    assert found is fake_container
    fake_client.containers.list.assert_called_once_with(
        all=True, filters={"label": "dvt.workspace=my-project"}
    )


def test_find_workspace_container_returns_none_when_absent():
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    assert find_workspace_container(fake_client, "missing") is None
