"""Real-runtime host<->container port-forwarding integration tests.

Opt-in only (`pixi run test integration`); skips cleanly when no Docker/Podman
engine is reachable. Mirrors tests/integration/test_native_runtime_lifecycle.py.
"""

from __future__ import annotations

import http.client
import json
import time
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from devtemplate.cli import app
from devtemplate.forward import build_forwarder
from devtemplate.runtime import get_client

runner = CliRunner()
pytestmark = pytest.mark.integration
runtime_unreachable = get_client("auto").is_err()

CONNECTOR_IMAGE = "python:3.12-alpine"  # ships python3; busybox `nc` also present


def _project(tmp_path: Path, extra: dict) -> Path:
    d = tmp_path / ".devcontainer"
    d.mkdir()
    (d / "devcontainer.json").write_text(
        json.dumps({"name": "dvt-fwd-test", "image": CONNECTOR_IMAGE, **extra})
    )
    return tmp_path


def _get(port: int, tries: int = 40) -> str:
    last = None
    for _ in range(tries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/")
            body = conn.getresponse().read().decode(errors="replace")
            conn.close()
            return body
        except OSError as exc:  # not up yet
            last = exc
            time.sleep(0.25)
    raise AssertionError(f"nothing answered on 127.0.0.1:{port}: {last}")


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_dvt_forward_reaches_an_in_container_server(tmp_path, monkeypatch):
    ws = f"dvt-fwd-{uuid.uuid4().hex[:8]}"
    monkeypatch.chdir(_project(tmp_path, {}))
    handle = get_client("auto").unwrap()
    try:
        assert runner.invoke(app, ["up", ws]).exit_code == 0
        # Leave an HTTP server listening on :2718 inside the container.
        started = runner.invoke(
            app,
            [
                "run",
                "-n",
                ws,
                "sh",
                "-c",
                "(python3 -m http.server 2718 >/dev/null 2>&1 &) ; sleep 1",
            ],
        )
        assert started.exit_code == 0, started.output

        fwd = build_forwarder(handle.client, handle.cli_binary, ws, ["2718"]).unwrap()
        try:
            body = _get(2718)
            assert "Directory listing" in body
        finally:
            fwd.close()
    finally:
        runner.invoke(app, ["delete", ws])


@pytest.mark.skipif(runtime_unreachable, reason="no Docker/Podman runtime reachable")
def test_appPort_is_published_and_drift_demands_rebuild(tmp_path, monkeypatch):
    ws = f"dvt-appport-{uuid.uuid4().hex[:8]}"
    project = _project(
        tmp_path,
        {
            "appPort": [2719],
            "postStartCommand": "python3 -m http.server 2719 >/dev/null 2>&1 &",
        },
    )
    monkeypatch.chdir(project)
    try:
        assert runner.invoke(app, ["up", ws]).exit_code == 0
        assert "Directory listing" in _get(2719)

        cfg = project / ".devcontainer" / "devcontainer.json"
        cfg.write_text(cfg.read_text().replace("2719", "2720"))
        drifted = runner.invoke(app, ["up", ws])
        assert drifted.exit_code != 0
        assert "--rebuild" in drifted.output
    finally:
        runner.invoke(app, ["delete", ws])
