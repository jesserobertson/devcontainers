from __future__ import annotations

import shlex
from unittest.mock import MagicMock

# `ssh.py` imports devtemplate.sshd lazily, inside stdio_proxy's body, to keep
# asyncssh/cryptography off every `dvt` invocation's import path - so the module
# object itself is what these tests patch, not an attribute of `devtemplate.ssh`.
# The lazy import re-resolves it from sys.modules per call, so this still lands.
from devtemplate import ssh as ssh_module
from devtemplate import sshd as sshd_module
from devtemplate.ssh import (
    exec_command,
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
        "-c",
        'exec "${SHELL:-sh}"',
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


def test_exec_command_returns_err_when_container_lookup_raises(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.side_effect = RuntimeError("daemon unreachable")

    result = exec_command(
        "/usr/bin/docker", fake_client, "my-project", ["pytest", "-q"], tty=False
    )

    assert result.is_err()


def test_exec_command_returns_err_when_no_container_found(monkeypatch):
    fake_client = MagicMock()
    fake_client.containers.list.return_value = []

    result = exec_command(
        "/usr/bin/docker", fake_client, "missing", ["pytest"], tty=False
    )

    assert result.is_err()


def test_exec_command_runs_via_interactive_login_shell_without_tty(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = exec_command(
        "/usr/bin/docker", fake_client, "my-project", ["pytest", "-q"], tty=False
    )

    assert result.is_ok()
    assert result.unwrap() == 0
    assert captured["args"] == [
        "/usr/bin/docker",
        "exec",
        "-i",
        "dvt-my-project",
        "sh",
        "-c",
        "exec \"${SHELL:-sh}\" -ilc 'pytest -q'",
    ]


def test_exec_command_adds_tty_flag_when_requested(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return MagicMock(returncode=0)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = exec_command(
        "/usr/bin/docker", fake_client, "my-project", ["python"], tty=True
    )

    assert result.is_ok()
    assert captured["args"][:4] == [
        "/usr/bin/docker",
        "exec",
        "-it",
        "dvt-my-project",
    ]


def test_exec_command_shell_quotes_command_tokens_with_spaces(monkeypatch):
    # The command reaches `sh -c` as a single argument, so a token containing
    # shell metacharacters (here a space and quotes) must survive intact rather
    # than being re-split by the container's shell.
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    captured = {}
    monkeypatch.setattr(
        ssh_module.subprocess,
        "run",
        lambda args: captured.update(args=args) or MagicMock(returncode=0),
    )

    exec_command(
        "/usr/bin/docker",
        fake_client,
        "my-project",
        ["python", "-c", "print('hi there')"],
        tty=False,
    )

    inner = captured["args"][-1]
    quoted_program = shlex.quote(shlex.join(["python", "-c", "print('hi there')"]))
    assert inner == f'exec "${{SHELL:-sh}}" -ilc {quoted_program}'


def test_exec_command_returns_err_when_subprocess_run_raises(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    def fake_run(args):
        raise FileNotFoundError("docker binary not found")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = exec_command(
        "/usr/bin/docker", fake_client, "my-project", ["pytest"], tty=False
    )

    assert result.is_err()


def test_exec_command_propagates_child_exit_code(monkeypatch):
    fake_client = MagicMock()
    fake_container = MagicMock()
    fake_container.name = "dvt-my-project"
    fake_client.containers.list.return_value = [fake_container]

    monkeypatch.setattr(
        ssh_module.subprocess, "run", lambda args: MagicMock(returncode=7)
    )

    result = exec_command(
        "/usr/bin/docker", fake_client, "my-project", ["false"], tty=False
    )

    assert result.is_ok()
    assert result.unwrap() == 7


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
        sshd_module,
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
        sshd_module,
        "run_stdio_server",
        lambda cli_binary, container_name: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = stdio_proxy("/usr/bin/docker", fake_client, "my-project")

    assert result.is_err()
