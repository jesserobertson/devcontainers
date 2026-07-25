from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

import docker
from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate import podman_machine


@dataclass(frozen=True)
class RuntimeHandle:
    """A resolved container runtime: a docker-py client plus which engine and CLI
    binary it talks to. cli_binary is only used by ssh.py's interactive exec
    plumbing, which shells out to the bundled docker/podman CLI rather than
    proxying raw stdio through docker-py's own exec/attach socket API - see
    ssh.py's module docstring. machine_name is set only for a Windows Podman
    machine (see podman_machine.py); None for Docker or non-Windows Podman."""

    client: DockerClient
    engine: Literal["docker", "podman"]
    cli_binary: str
    machine_name: str | None = None


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
    """Best-effort default rootless Podman socket path on Linux. Also wrong for
    a macOS podman-machine setup - a separate, pre-existing gap, out of scope
    for this plan (see Global Constraints)."""
    if sys.platform == "win32" or not hasattr(os, "getuid"):
        return None
    return f"unix:///run/user/{os.getuid()}/podman/podman.sock"


def _resolve_podman(
    *, auto_init: bool, auto_start: bool
) -> Result[RuntimeHandle, Exception]:
    cli_binary = shutil.which("podman")
    if cli_binary is None:
        return Err(FileNotFoundError("podman not found on PATH"))
    if sys.platform == "win32":
        machine_result = podman_machine.ensure_machine_ready(
            cli_binary, auto_start=auto_start, auto_init=auto_init
        )
        if machine_result.is_err():
            return Err(machine_result.unwrap_err())
        machine_name, socket_url = machine_result.unwrap()
    else:
        machine_name = None
        socket_url = os.environ.get("CONTAINER_HOST") or _default_podman_socket()
        if socket_url is None:
            return Err(
                RuntimeError(
                    "Podman socket not found (tried CONTAINER_HOST / default rootless path)"
                )
            )
    try:
        client = docker.DockerClient(base_url=socket_url)
        client.ping()
    except Exception as exc:
        return Err(exc)
    return Ok(
        RuntimeHandle(
            client=client,
            engine="podman",
            cli_binary=cli_binary,
            machine_name=machine_name,
        )
    )


def _try_podman(
    *, auto_init: bool = False, auto_start: bool = True
) -> RuntimeHandle | None:
    result = _resolve_podman(auto_init=auto_init, auto_start=auto_start)
    return result.unwrap() if result.is_ok() else None


def get_client(
    runtime: Literal["auto", "docker", "podman"],
    *,
    podman_machine_auto_init: bool = False,
    podman_machine_auto_start: bool = True,
) -> Result[RuntimeHandle, Exception]:
    """Resolve a container runtime per Settings.runtime. "auto" tries Docker's
    endpoint first, then Podman's compatible socket. An explicit "podman"
    request surfaces Podman-specific errors directly (e.g. "no machine found")
    rather than the generic message "auto" falls back to on double failure."""
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
        return _resolve_podman(
            auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
        )
    handle = _try_docker() or _try_podman(
        auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
    )
    if handle is None:
        return Err(RuntimeError("No container runtime found (tried Docker, Podman)"))
    return Ok(handle)
