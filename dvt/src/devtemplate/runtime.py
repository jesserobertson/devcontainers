from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

import docker
from docker.client import DockerClient
from logerr import Err, Ok, Result  # noqa: F401
from logerr.utilities import nullable, wrap_result

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


__all__ = ["RuntimeHandle", "get_client"]


def try_docker() -> RuntimeHandle | None:
    cli_binary = shutil.which("docker")
    if cli_binary is None:
        return None
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None
    return RuntimeHandle(client=client, engine="docker", cli_binary=cli_binary)


def default_podman_socket() -> str | None:
    """Best-effort default rootless Podman socket path on Linux. Also wrong for
    a macOS podman-machine setup - a separate, pre-existing gap, out of scope
    for this plan (see Global Constraints)."""
    if sys.platform == "win32" or not hasattr(os, "getuid"):
        return None
    return f"unix:///run/user/{os.getuid()}/podman/podman.sock"


@wrap_result
def resolve_podman(*, auto_init: bool, auto_start: bool) -> RuntimeHandle:
    cli_binary = shutil.which("podman")
    if cli_binary is None:
        raise FileNotFoundError("podman not found on PATH")
    if sys.platform == "win32":
        machine_name, socket_url = podman_machine.ensure_machine_ready(
            cli_binary, auto_start=auto_start, auto_init=auto_init
        ).unwrap()
    else:
        machine_name = None
        socket_url = os.environ.get("CONTAINER_HOST") or default_podman_socket()
        if socket_url is None:
            raise RuntimeError(
                "Podman socket not found (tried CONTAINER_HOST / default rootless path)"
            )
    client = docker.DockerClient(base_url=socket_url)
    client.ping()
    return RuntimeHandle(
        client=client,
        engine="podman",
        cli_binary=cli_binary,
        machine_name=machine_name,
    )


def try_podman(
    *, auto_init: bool = False, auto_start: bool = True
) -> RuntimeHandle | None:
    result = resolve_podman(auto_init=auto_init, auto_start=auto_start)
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
        return nullable(
            try_docker(),
            error_factory=lambda: RuntimeError(
                "Docker not reachable (tried DOCKER_HOST / platform default)"
            ),
            return_type="result",
        )
    if runtime == "podman":
        return resolve_podman(
            auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
        )
    handle = try_docker() or try_podman(
        auto_init=podman_machine_auto_init, auto_start=podman_machine_auto_start
    )
    return nullable(
        handle,
        error_factory=lambda: RuntimeError(
            "No container runtime found (tried Docker, Podman)"
        ),
        return_type="result",
    )
