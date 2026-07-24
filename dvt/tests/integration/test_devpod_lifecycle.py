"""Real devpod lifecycle integration test.

Opt-in only — run with `pixi run test integration`, never part of `pixi run test all`,
`pixi run pytest`, or CI. Requires `devpod` on PATH and a working container runtime
(skips cleanly, not a failure, if devpod isn't installed).

Deliberately does NOT test `ssh`: a real `devpod ssh` permission error was observed during
manual testing that reproduced identically calling bare `devpod ssh` directly, with no dvt
code involved at all — confirming it isn't something a dvt-level test can meaningfully
assert on. ssh's command construction is already covered by dvt/tests/test_cli.py's mocked
tests.

Deliberately uses a minimal, self-authored devcontainer.json (a plain public base image, no
custom features, no postCreateCommand) rather than a template synced from GitHub. This
isolates what this test verifies — does dvt correctly drive a real devpod through a
container lifecycle — from a separate, already-flagged bug in this repo's own
features/*/install.sh scripts (unrelated to dvt): their "copy a default pixi.toml if none
exists" logic runs at image-build time, before the real project directory is bind-mounted
at /workspace at container start, so the default gets shadowed and postCreateCommand: pixi
install fails for any project with no pixi.toml of its own. That bug belongs to a different,
already-completed plan.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app

runner = CliRunner()

pytestmark = pytest.mark.integration

devpod_missing = shutil.which("devpod") is None


@pytest.fixture
def real_project(tmp_path: Path) -> Path:
    """A minimal, self-contained devcontainer.json project — no dvt template involved."""
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
    return tmp_path


@pytest.mark.skipif(devpod_missing, reason="devpod not installed")
def test_up_stop_delete_lifecycle(real_project: Path) -> None:
    """Real devpod up -> stop -> delete against a real container runtime, no mocking."""
    workspace_id = f"dvt-integration-test-{uuid.uuid4().hex[:8]}"

    try:
        up_result = runner.invoke(
            app,
            ["up", str(real_project), "--", "--id", workspace_id, "--ide", "none"],
        )
        assert up_result.exit_code == 0, up_result.output

        stop_result = runner.invoke(app, ["stop", workspace_id])
        assert stop_result.exit_code == 0, stop_result.output
    finally:
        runner.invoke(app, ["delete", workspace_id])
