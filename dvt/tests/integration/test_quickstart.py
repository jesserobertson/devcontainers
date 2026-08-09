"""Integration tests that run docs/content/quickstart.md's own command
sequence against the real jesserobertson/devcontainers GitHub repo and its
real GHCR-published Features - never a fake local template.

Opt-in only - run with `pixi run test integration`, never part of `pixi run test
all`, `pixi run pytest`, or CI. Requires network access and a reachable Docker
or Podman engine (skips cleanly, not a failure, if the runtime is unreachable;
a missing network connection surfaces as a real failure from `template sync`,
since this suite's whole point is exercising the real thing).

Written after a live run-through of the quickstart surfaced four real bugs
that no unit test caught, because each one only manifests against a real base
image, a real published Feature, and a real container runtime:

- build.py's generated Dockerfile inherited the base image's own trailing
  `USER dev` for Feature install RUN steps, instead of the spec-mandated
  root - broke any Feature (like `cli`) that needs root during install.
- container.py never substituted devcontainer.json's `${localWorkspaceFolder}`
  variable in workspaceMount, so Docker/Podman misread the literal
  `${localWorkspaceFolder}` as an (invalid) named-volume name.
- `dvt project init` never scaffolded a pixi.toml, so every template's
  `postCreateCommand: "pixi install"` failed outright with nothing to install
  against - and even once scaffolded, installing straight into
  <project>/.pixi/envs (the workspaceMount bind mount) failed permission
  checks on at least Podman's WSL2 machine on Windows, fixed by turning on
  pixi's detached-environments config first.
- `dvt ssh` hardcoded `sh` for interactive sessions instead of the container
  user's real configured shell, so an image's own shell-startup hooks (this
  base image's `pixi shell-hook` in .bashrc/fish's conf.d) never fired.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client

runner = CliRunner()

pytestmark = pytest.mark.integration

runtime_unreachable = get_client("auto").is_err()


def test_template_sync_and_list(settings) -> None:
    """Quickstart steps 1-2: `dvt template sync` then `dvt template list`
    against the real GitHub repo - no container runtime needed."""
    sync_result = runner.invoke(app, ["template", "sync"])
    assert sync_result.exit_code == 0, sync_result.output
    assert "cli" in sync_result.output

    list_result = runner.invoke(app, ["template", "list"])
    assert list_result.exit_code == 0, list_result.output
    assert "cli" in list_result.output


def test_project_init_and_add_feature(settings, tmp_path, monkeypatch) -> None:
    """Quickstart steps 3-4: scaffold from a real template, then layer a
    second real template's requirements in via add-feature. Config-level
    only, no container build - the full build+run+ssh cycle for one template
    is covered by test_quickstart_cli_template_full_lifecycle below."""
    sync_result = runner.invoke(app, ["template", "sync"])
    assert sync_result.exit_code == 0, sync_result.output

    project_dir = tmp_path / "my-cli-project"
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(
        app, ["project", "init", str(project_dir), "--template", "cli"]
    )
    assert init_result.exit_code == 0, init_result.output

    devcontainer_path = project_dir / ".devcontainer" / "devcontainer.json"
    devcontainer = json.loads(devcontainer_path.read_text())
    assert devcontainer["name"] == "my-cli-project"
    # pixi.toml scaffolding + the workspace-table fix, see project.py.
    pixi_toml = (project_dir / "pixi.toml").read_text()
    assert "[workspace]" in pixi_toml

    monkeypatch.chdir(project_dir)
    add_result = runner.invoke(app, ["project", "add-feature", "py-devtools"])
    assert add_result.exit_code == 0, add_result.output

    merged = json.loads(devcontainer_path.read_text())
    assert merged["name"] == "my-cli-project"
    assert len(merged["features"]) == 2


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_quickstart_cli_template_full_lifecycle(
    settings, tmp_path, monkeypatch
) -> None:
    """Quickstart steps 3, 5-7 end to end against the real `cli` template:

    - real GHCR Feature pull + image build (exercises build.py's USER root fix)
    - real workspaceMount with ${localWorkspaceFolder} (exercises
      container.py's variable-substitution fix)
    - a real postCreateCommand `pixi install` against dvt's own scaffolded
      pixi.toml, kept off the workspaceMount bind mount (exercises
      project.py's pixi.toml + detached-environments fixes)
    - a real `ssh` client through the actual ~/.ssh/config ProxyCommand entry
      `up` writes (proves the ssh_server.py bridge works against a real
      Feature-built image, not just the synthetic alpine one
      test_native_runtime_lifecycle.py uses)
    """
    sync_result = runner.invoke(app, ["template", "sync"])
    assert sync_result.exit_code == 0, sync_result.output

    project_dir = tmp_path / "dvt-test-cli"
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(
        app, ["project", "init", str(project_dir), "--template", "cli"]
    )
    assert init_result.exit_code == 0, init_result.output

    workspace_name = f"dvt-quickstart-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(project_dir)

    try:
        up_result = runner.invoke(app, ["up", workspace_name])
        assert up_result.exit_code == 0, up_result.output

        # Direct exec_run, independent of the `dvt ssh` path exercised below:
        # proves postCreateCommand's `pixi install` actually produced a
        # working project environment.
        handle = get_client("auto").unwrap()
        container = find_workspace_container(handle.client, workspace_name)
        assert container is not None
        exit_code, output = container.exec_run(
            ["sh", "-c", "cd /workspace && pixi run python --version"]
        )
        output_text = output.decode(errors="replace")
        assert exit_code == 0, output_text
        assert "Python" in output_text

        ssh_binary = shutil.which("ssh")
        if ssh_binary is not None:
            ssh_result = subprocess.run(
                [
                    ssh_binary,
                    "-F",
                    str(Path.home() / ".ssh" / "config"),
                    workspace_name,
                    "echo hello-from-quickstart",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert ssh_result.returncode == 0, ssh_result.stderr
            assert "hello-from-quickstart" in ssh_result.stdout

        stop_result = runner.invoke(app, ["stop", workspace_name])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_name])
