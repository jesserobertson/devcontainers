from __future__ import annotations

from devtemplate import runtime as runtime_module


class _FakeClient:
    def __init__(self, reachable: bool = True):
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise ConnectionError("not reachable")
        return True


def test_get_client_docker_success(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "docker" else None,
    )
    monkeypatch.setattr(runtime_module.docker, "from_env", lambda: _FakeClient())

    result = runtime_module.get_client("docker")

    assert result.is_ok()
    handle = result.unwrap()
    assert handle.engine == "docker"
    assert handle.cli_binary == "/usr/bin/docker"


def test_get_client_docker_unreachable_is_err(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        runtime_module.docker, "from_env", lambda: _FakeClient(reachable=False)
    )

    result = runtime_module.get_client("docker")

    assert result.is_err()


def test_get_client_docker_missing_binary_is_err(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: None)

    result = runtime_module.get_client("docker")

    assert result.is_err()


def test_get_client_podman_uses_container_host(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "podman" else None,
    )
    monkeypatch.setattr(runtime_module.sys, "platform", "linux")
    monkeypatch.setenv("CONTAINER_HOST", "unix:///tmp/podman.sock")
    captured = {}

    def fake_docker_client(base_url):
        captured["base_url"] = base_url
        return _FakeClient()

    monkeypatch.setattr(runtime_module.docker, "DockerClient", fake_docker_client)

    result = runtime_module.get_client("podman")

    assert result.is_ok()
    assert captured["base_url"] == "unix:///tmp/podman.sock"
    assert result.unwrap().engine == "podman"


def test_get_client_auto_falls_back_to_podman(monkeypatch):
    monkeypatch.setattr(
        runtime_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("docker", "podman") else None,
    )
    monkeypatch.setattr(runtime_module.sys, "platform", "linux")
    monkeypatch.setattr(
        runtime_module.docker, "from_env", lambda: _FakeClient(reachable=False)
    )
    monkeypatch.setenv("CONTAINER_HOST", "unix:///tmp/podman.sock")
    monkeypatch.setattr(
        runtime_module.docker, "DockerClient", lambda base_url: _FakeClient()
    )

    result = runtime_module.get_client("auto")

    assert result.is_ok()
    assert result.unwrap().engine == "podman"


def test_get_client_auto_err_when_nothing_reachable(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: None)

    result = runtime_module.get_client("auto")

    assert result.is_err()


def test_get_client_podman_explicit_surfaces_specific_windows_error(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_module.podman_machine,
        "ensure_machine_ready",
        lambda cli_binary, auto_start, auto_init: runtime_module.Err(
            RuntimeError("No Podman machine found. Run 'podman machine init' first.")
        ),
    )

    result = runtime_module.get_client("podman")

    assert result.is_err()
    assert "No Podman machine found" in str(result.unwrap_err())


def test_get_client_podman_windows_success_sets_machine_name(monkeypatch):
    monkeypatch.setattr(runtime_module.shutil, "which", lambda name: "/usr/bin/podman")
    monkeypatch.setattr(runtime_module.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_module.podman_machine,
        "ensure_machine_ready",
        lambda cli_binary, auto_start, auto_init: runtime_module.Ok(
            ("devpod-machine", "npipe:////./pipe/podman-devpod-machine")
        ),
    )
    captured = {}

    def fake_docker_client(base_url):
        captured["base_url"] = base_url
        return _FakeClient()

    monkeypatch.setattr(runtime_module.docker, "DockerClient", fake_docker_client)

    result = runtime_module.get_client(
        "podman", podman_machine_auto_init=True, podman_machine_auto_start=True
    )

    assert result.is_ok()
    handle = result.unwrap()
    assert handle.machine_name == "devpod-machine"
    assert captured["base_url"] == "npipe:////./pipe/podman-devpod-machine"
