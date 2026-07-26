"""Real container-runtime lifecycle integration test.

Opt-in only - run with `pixi run test integration`, never part of `pixi run test
all`, `pixi run pytest`, or CI. Requires a reachable Docker or Podman engine
(skips cleanly, not a failure, if neither is reachable).

Builds a real image (no Features, to keep the test fast and independent of
ghcr.io availability) from a minimal public base image, runs it, then stops and
deletes it - exercising the full native runtime path with no mocking.

Also drives a real `ssh` client binary (not `dvt ssh`, not asyncssh in-process)
through the actual `~/.ssh/config` entry `dvt up` writes, proving the
`ProxyCommand dvt ssh --stdio <name>` bridge (ssh_server.py) genuinely works
end to end against a live container - not just the in-process asyncssh
client/server tests. `dvt ssh <name>` (direct, without `ProxyCommand`) still
isn't exercised here: it execs `docker`/`podman exec -it`, which requires a
real TTY on the invoking process's stdin - something a CliRunner invocation
can't provide, so it isn't a fit for this non-interactive test.
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
from devtemplate.runtime import get_client

runner = CliRunner()

pytestmark = pytest.mark.integration

runtime_unreachable = get_client("auto").is_err()


@pytest.fixture
def real_project(tmp_path: Path) -> Path:
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "dvt-integration-test", "image": "alpine:latest"})
    )
    return tmp_path


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_up_stop_delete_lifecycle(real_project: Path, monkeypatch) -> None:
    workspace_name = f"dvt-integration-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(real_project)

    try:
        up_result = runner.invoke(app, ["up", workspace_name])
        assert up_result.exit_code == 0, up_result.output

        # Drive a real `ssh` client binary (not `dvt ssh`, not asyncssh
        # in-process) through the actual `~/.ssh/config` entry `up` just
        # wrote, proving the `ProxyCommand dvt ssh --stdio <name>` bridge
        # (ssh_server.py) genuinely works end to end against a live
        # container. Skipped gracefully (not a test failure) if `ssh` isn't
        # on PATH - most CI runners and dev machines have it, but the whole
        # lifecycle test shouldn't depend on it.
        ssh_binary = shutil.which("ssh")
        if ssh_binary is not None:
            ssh_result = subprocess.run(
                [
                    ssh_binary,
                    "-F",
                    str(Path.home() / ".ssh" / "config"),
                    workspace_name,
                    "echo hello-from-real-ssh",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert ssh_result.returncode == 0, ssh_result.stderr
            assert "hello-from-real-ssh" in ssh_result.stdout

        stop_result = runner.invoke(app, ["stop", workspace_name])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_name])
