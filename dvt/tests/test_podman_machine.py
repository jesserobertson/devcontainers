from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from devtemplate.podman_machine import (
    check_gpu_cdi_ready,
    ensure_gpu_support,
    ensure_machine_ready,
    inspect_machine,
    install_nvidia_toolkit,
    list_machines,
    start_machine,
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


def test_ensure_machine_ready_no_machines_auto_init_without_auto_start_does_not_start():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["machine", "list"]:
            return _fake_run(0, stdout="[]")
        if args[1:3] == ["machine", "init"]:
            return _fake_run(0)
        raise AssertionError(f"unexpected call: {args}")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_machine_ready("podman", auto_start=False, auto_init=True)

    assert result.is_err()
    assert any(c[1:3] == ["machine", "init"] for c in calls)
    assert not any(c[1:3] == ["machine", "start"] for c in calls)


def test_check_gpu_cdi_ready_true_when_exists():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="exists\n"),
    ):
        result = check_gpu_cdi_ready("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap() is True


def test_check_gpu_cdi_ready_false_when_missing():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(0, stdout="missing\n"),
    ):
        result = check_gpu_cdi_ready("podman", "devpod-machine")
    assert result.is_ok()
    assert result.unwrap() is False


def test_ensure_gpu_support_skips_install_when_already_ready():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _fake_run(0, stdout="exists\n")

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_gpu_support("podman", "devpod-machine")

    assert result.is_ok()
    assert len(calls) == 1  # only the check, no install


def test_ensure_gpu_support_installs_when_missing():
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "test -f /etc/cdi/nvidia.yaml" in args[-1]:
            return _fake_run(0, stdout="missing\n")
        return _fake_run(0)

    with patch("devtemplate.podman_machine.subprocess.run", side_effect=fake_run):
        result = ensure_gpu_support("podman", "devpod-machine")

    assert result.is_ok()
    assert len(calls) == 2
    assert "nvidia-ctk cdi generate" in calls[1][-1]


def test_install_nvidia_toolkit_returns_err_on_failure():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        return_value=_fake_run(1, stderr="ssh failed"),
    ):
        result = install_nvidia_toolkit("podman", "devpod-machine")
    assert result.is_err()


def test_start_machine_returns_err_on_timeout():
    with patch(
        "devtemplate.podman_machine.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="podman machine start", timeout=120),
    ):
        result = start_machine("podman", "devpod-machine")

    assert result.is_err()
    assert isinstance(result.unwrap_err(), subprocess.TimeoutExpired)
