from __future__ import annotations

from unittest.mock import MagicMock

from devtemplate import ssh as ssh_module
from devtemplate.ssh import exec_interactive


def test_exec_interactive_returns_err_when_container_lookup_raises(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.side_effect = RuntimeError("daemon unreachable")

    exit_code_result = exec_interactive("/usr/bin/docker", fake_client, "my-project")

    assert exit_code_result.is_err()


def test_exec_interactive_returns_err_when_no_container_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    exit_code_result = exec_interactive("/usr/bin/docker", fake_client, "missing")

    assert exit_code_result.is_err()


def test_exec_interactive_uses_tty_flags(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    exit_code_result = exec_interactive("/usr/bin/docker", fake_client, "my-project")

    assert exit_code_result.is_ok()
    assert exit_code_result.unwrap() == 0
    assert captured["args"] == [
        "/usr/bin/docker",
        "exec",
        "-it",
        "dvt-my-project",
        "sh",
    ]


def test_exec_interactive_returns_err_when_subprocess_run_raises(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    def fake_run(args):
        raise FileNotFoundError("docker binary not found")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    exit_code_result = exec_interactive("/usr/bin/docker", fake_client, "my-project")

    assert exit_code_result.is_err()
