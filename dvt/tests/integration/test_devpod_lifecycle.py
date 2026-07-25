"""Real container-runtime lifecycle integration test.

Opt-in only — run with `pixi run test integration`, never part of `pixi run test all`,
`pixi run pytest`, or CI. Requires a reachable Docker/Podman runtime (skips cleanly, not
a failure, if none is reachable).

Deliberately does NOT test `ssh`: exec'ing a real shell through docker/podman is already
covered indirectly by dvt/tests/test_ssh.py's mocked tests, and a real interactive/stdio
exec doesn't lend itself to a non-interactive assertion here.

Deliberately uses a minimal, self-authored devcontainer.json (a plain public base image, no
custom features, no postCreateCommand) rather than a template synced from GitHub. This
isolates what this test verifies — does dvt correctly drive a real container runtime through
a container lifecycle — from a separate, already-flagged bug in this repo's own
features/*/install.sh scripts (unrelated to dvt): their "copy a default pixi.toml if none
exists" logic runs at image-build time, before the real project directory is bind-mounted
at /workspace at container start, so the default gets shadowed and postCreateCommand: pixi
install fails for any project with no pixi.toml of its own. That bug belongs to a different,
already-completed plan.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.config import load_settings
from devtemplate.runtime import get_client

runner = CliRunner()

pytestmark = pytest.mark.integration


def _no_runtime_reachable() -> bool:
    settings_result = load_settings()
    if settings_result.is_err():
        return True
    return get_client(settings_result.unwrap().runtime).is_err()


runtime_missing = _no_runtime_reachable()


@pytest.fixture
def real_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal, self-contained devcontainer.json project - no dvt template involved."""
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "dvt-integration-test",
                "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.skipif(runtime_missing, reason="no Docker/Podman runtime reachable")
def test_up_stop_delete_lifecycle(real_project: Path) -> None:
    """Real up -> stop -> delete against a real container runtime, no mocking."""
    workspace_id = f"dvt-integration-test-{uuid.uuid4().hex[:8]}"

    try:
        up_result = runner.invoke(app, ["up", workspace_id])
        assert up_result.exit_code == 0, up_result.output

        stop_result = runner.invoke(app, ["stop", workspace_id])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_id])
