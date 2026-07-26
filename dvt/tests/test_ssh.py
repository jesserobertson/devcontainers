from __future__ import annotations

from unittest.mock import MagicMock

from devtemplate import ssh as ssh_module
from devtemplate.ssh import (
    exec_interactive,
    remove_ssh_config_entry,
    stdio_proxy,
    write_ssh_config_entry,
)


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


def test_write_ssh_config_entry_adds_host_block(tmp_path):
    config_path = tmp_path / "config"
    config_path.write_text("Host existing\n    HostName example.com\n")

    result = write_ssh_config_entry("my-project", config_path)

    assert result.is_ok()
    content = config_path.read_text()
    assert "Host existing" in content
    assert "Host my-project" in content
    assert "ProxyCommand dvt ssh --stdio my-project" in content


def test_write_ssh_config_entry_is_idempotent(tmp_path):
    config_path = tmp_path / "config"
    write_ssh_config_entry("my-project", config_path)
    write_ssh_config_entry("my-project", config_path)
    assert config_path.read_text().count("Host my-project") == 1


def test_remove_ssh_config_entry_removes_block_only(tmp_path):
    config_path = tmp_path / "config"
    write_ssh_config_entry("keep-me", config_path)
    write_ssh_config_entry("remove-me", config_path)

    result = remove_ssh_config_entry("remove-me", config_path)

    assert result.is_ok()
    content = config_path.read_text()
    assert "Host keep-me" in content
    assert "Host remove-me" not in content


def test_remove_ssh_config_entry_noop_when_file_absent(tmp_path):
    result = remove_ssh_config_entry("anything", tmp_path / "nonexistent")
    assert result.is_ok()


def test_stdio_proxy_runs_real_ssh_server(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]
    captured = {}
    monkeypatch.setattr(
        ssh_module.ssh_server,
        "run_stdio_server",
        # dict.setdefault(k, v) returns v itself, which would short-circuit `or`
        # and make this lambda return the captured tuple instead of 0 - use
        # update() (which returns None) so the return value is genuinely 0.
        lambda cli_binary, container_name: (
            captured.update(args=(cli_binary, container_name)) or 0
        ),
    )

    result = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert result.is_ok()
    assert result.unwrap() == 0
    assert captured["args"] == ("/usr/bin/docker", "dvt-my-project")


def test_stdio_proxy_returns_err_when_no_container_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    result = stdio_proxy("/usr/bin/docker", fake_client, "missing")

    assert result.is_err()


def test_stdio_proxy_returns_err_when_server_raises(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]
    monkeypatch.setattr(
        ssh_module.ssh_server,
        "run_stdio_server",
        lambda cli_binary, container_name: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert result.is_err()
