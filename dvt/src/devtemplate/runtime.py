from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

import docker
from docker.client import DockerClient
from logerr import Err, Ok, Result


@dataclass(frozen=True)
class RuntimeHandle:
    """A resolved container runtime: a docker-py client plus which engine and CLI
    binary it talks to. cli_binary is only used by ssh.py's interactive exec
    plumbing, which shells out to the bundled docker/podman CLI rather than
    proxying raw stdio through docker-py's own exec/attach socket API - see
    ssh.py's module docstring."""

    client: DockerClient
    engine: Literal["docker", "podman"]
    cli_binary: str


def _try_docker() -> RuntimeHandle | None:
    cli_binary = shutil.which("docker")
    if cli_binary is None:
        return None
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None
    return RuntimeHandle(client=client, engine="docker", cli_binary=cli_binary)


def _default_podman_socket() -> str | None:
    """Best-effort default rootless Podman socket path on Linux/macOS. Podman on
    Windows (WSL2-backed `podman machine`) isn't covered - CONTAINER_HOST must be
    set explicitly there. See the design spec's Known Gaps."""
    if sys.platform == "win32" or not hasattr(os, "getuid"):
        return None
    return f"unix:///run/user/{os.getuid()}/podman/podman.sock"


def _try_podman() -> RuntimeHandle | None:
    cli_binary = shutil.which("podman")
    if cli_binary is None:
        return None
    socket_url = os.environ.get("CONTAINER_HOST") or _default_podman_socket()
    if socket_url is None:
        return None
    try:
        client = docker.DockerClient(base_url=socket_url)
        client.ping()
    except Exception:
        return None
    return RuntimeHandle(client=client, engine="podman", cli_binary=cli_binary)


def get_client(
    runtime: Literal["auto", "docker", "podman"],
) -> Result[RuntimeHandle, Exception]:
    """Resolve a container runtime per Settings.runtime. "auto" tries Docker's
    endpoint first, then Podman's compatible socket."""
    if runtime == "docker":
        handle = _try_docker()
        if handle is None:
            return Err(
                RuntimeError(
                    "Docker not reachable (tried DOCKER_HOST / platform default)"
                )
            )
        return Ok(handle)
    if runtime == "podman":
        handle = _try_podman()
        if handle is None:
            return Err(
                RuntimeError(
                    "Podman not reachable (tried CONTAINER_HOST / default rootless socket)"
                )
            )
        return Ok(handle)
    handle = _try_docker() or _try_podman()
    if handle is None:
        return Err(RuntimeError("No container runtime found (tried Docker, Podman)"))
    return Ok(handle)
