from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from devtemplate.podman_machine import (
    ensure_machine_ready,
    inspect_machine,
    list_machines,
)

RUNNING_MACHINE = {
    "Name": "devpod-machine",
    "Default": True,
    "Running": True,
    "VMType": "wsl",
}

STOPPED_MACHINE = {**RUNNING_MACHINE, "Running": False}

INSPECT_RUNNING = [
    {
        "Name": "devpod-machine",
        "State": "running",
        "ConnectionInfo": {
            "PodmanPipe": {"Path": r"\\.\pipe\podman-devpod-machine"},
            "PodmanSocket": {"Path": "/tmp/podman/devpod-machine-api.sock"},
        },
    }
]

INSPECT_STOPPED = [{**INSPECT_RUNNING[0], "State": "stopped"}]


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_list_machines_parses_json():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout=json.dumps([RUNNING_MACHINE])),
    ):
        result = list_machines("podman")
    assert result.is_ok()
    assert result.unwrap() == [RUNNING_MACHINE]


def test_list_machines_returns_err_on_failure():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(1, stderr="boom"),
    ):
        result = list_machines("podman")
    assert result.is_err()


def test_inspect_machine_parses_first_entry():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout=json.dumps(INSPECT_RUNNING)),
    ):
        result = inspect_machine("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap()["State"] == "running"


def test_ensure_machine_ready_no_machines_refuses_without_auto_init():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="[]"),
    ):
        result = ensure_machine_ready("podman", auto_start=True, auto_init=False)
    assert result.is_err()


def test_ensure_machine_ready_already_running_resolves_connection_url():
    def fake_run(args, **kwargs):
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([RUNNING_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_RUNNING))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_machine_ready("podman", auto_start=True, auto_init=False)

    assert result.is_ok()
    name, url = result.unwrap()
    assert name == "devpod-machine"
    assert url == "npipe:////./pipe/podman-devpod-machine"


def test_ensure_machine_ready_stopped_without_auto_start_refuses():
    def fake_run(args, **kwargs):
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([STOPPED_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_STOPPED))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_machine_ready("podman", auto_start=False, auto_init=False)

    assert result.is_err()


def test_ensure_machine_ready_stopped_with_auto_start_starts_it():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout=json.dumps([STOPPED_MACHINE]))
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_STOPPED))
        if args[1:3] == ["machine", "start"]:
            return _fake_run(0)
        if args[1] == "ps":
            return _fake_run(0, stdout="[]")
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        with patch("devtemplate.podman_machine.time.sleep"):
            result = ensure_machine_ready("podman", auto_start=True, auto_init=False)

    assert result.is_ok()
    assert ["podman", "machine", "start", "devpod-machine"] in calls


def test_ensure_machine_ready_no_machines_with_auto_init_creates_one():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout="[]")
        if args[1:3] == ["machine", "init"]:
            return _fake_run(0)
        if args[1:3] == ["machine", "start"]:
            return _fake_run(0)
        if args[1] == "ps":
            return _fake_run(0, stdout="[]")
        if args[1:3] == ["machine", "inspect"]:
            return _fake_run(0, stdout=json.dumps(INSPECT_RUNNING))
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        with patch("devtemplate.podman_machine.time.sleep"):
            result = ensure_machine_ready("podman", auto_start=True, auto_init=True)

    assert result.is_ok()
    assert any(c[1:3] == ["machine", "init"] for c in calls)
