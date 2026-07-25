from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from logerr import Err, Ok, Result

_DEFAULT_MACHINE_NAME = "dvt-machine"
_DEFAULT_CPUS = 2
_DEFAULT_MEMORY_MB = 4096
_DEFAULT_DISK_GB = 100
_NAMED_PIPE_PATTERN = re.compile(r"\\\\\.\\pipe\\(.+)$")


def _run_podman_json(cli_binary: str, args: list[str]) -> Result[Any, Exception]:
    try:
        result = subprocess.run([cli_binary, *args], capture_output=True, text=True)
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"podman {' '.join(args)} failed (exit {result.returncode}): "
                    f"{result.stderr.strip()}"
                )
            )
        return Ok(json.loads(result.stdout))
    except Exception as exc:
        return Err(exc)


def list_machines(cli_binary: str) -> Result[list[dict[str, Any]], Exception]:
    return _run_podman_json(cli_binary, ["machine", "list", "--format", "json"])


def inspect_machine(cli_binary: str, name: str) -> Result[dict[str, Any], Exception]:
    try:
        result = _run_podman_json(cli_binary, ["machine", "inspect", name])
        if result.is_err():
            return Err(result.unwrap_err())
        inspected = result.unwrap()
        if (
            not isinstance(inspected, list)
            or not inspected
            or not isinstance(inspected[0], dict)
        ):
            return Err(
                ValueError(
                    f"podman machine inspect {name!r} returned unexpected shape: {inspected!r}"
                )
            )
        return Ok(inspected[0])
    except Exception as exc:
        return Err(exc)


def start_machine(cli_binary: str, name: str) -> Result[None, Exception]:
    try:
        result = subprocess.run(
            [cli_binary, "machine", "start", name], capture_output=True, text=True
        )
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"Failed to start machine {name!r}: {result.stderr.strip()}"
                )
            )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def init_machine(cli_binary: str, name: str) -> Result[None, Exception]:
    try:
        result = subprocess.run(
            [
                cli_binary,
                "machine",
                "init",
                name,
                "--cpus",
                str(_DEFAULT_CPUS),
                "--memory",
                str(_DEFAULT_MEMORY_MB),
                "--disk-size",
                str(_DEFAULT_DISK_GB),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return Err(
                RuntimeError(
                    f"Failed to init machine {name!r}: {result.stderr.strip()}"
                )
            )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def wait_until_ready(
    cli_binary: str, timeout_seconds: int = 60
) -> Result[None, Exception]:
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = subprocess.run([cli_binary, "ps"], capture_output=True, text=True)
            if result.returncode == 0:
                return Ok(None)
            time.sleep(2)
        return Err(
            RuntimeError(f"Machine did not become ready within {timeout_seconds}s")
        )
    except Exception as exc:
        return Err(exc)


def _connection_url(inspected: dict[str, Any]) -> Result[str, Exception]:
    """Translate `podman machine inspect`'s ConnectionInfo into a docker-py
    base_url. Verified directly against a real machine: PodmanPipe.Path looks
    like '\\\\.\\pipe\\podman-devpod-machine' on Windows; docker-py expects
    'npipe:////./pipe/podman-devpod-machine'. Falls back to PodmanSocket for
    non-Windows callers of this function (WSL-internal use), though this
    module is only invoked from runtime.py on win32 - see Global Constraints."""
    try:
        connection_info = inspected.get("ConnectionInfo")
        if not isinstance(connection_info, dict):
            return Err(
                ValueError(
                    f"machine inspect result has no ConnectionInfo: {inspected!r}"
                )
            )
        pipe = connection_info.get("PodmanPipe")
        if isinstance(pipe, dict) and isinstance(pipe.get("Path"), str):
            match = _NAMED_PIPE_PATTERN.match(pipe["Path"])
            if match:
                return Ok(f"npipe:////./pipe/{match.group(1)}")
        socket_info = connection_info.get("PodmanSocket")
        if isinstance(socket_info, dict) and isinstance(socket_info.get("Path"), str):
            return Ok(f"unix://{socket_info['Path']}")
        return Err(
            ValueError(
                f"machine inspect result has no usable connection endpoint: {connection_info!r}"
            )
        )
    except Exception as exc:
        return Err(exc)


def _inspect_and_connect(
    cli_binary: str, name: str
) -> Result[tuple[str, str], Exception]:
    try:
        inspect_result = inspect_machine(cli_binary, name)
        if inspect_result.is_err():
            return Err(inspect_result.unwrap_err())
        url_result = _connection_url(inspect_result.unwrap())
        if url_result.is_err():
            return Err(url_result.unwrap_err())
        return Ok((name, url_result.unwrap()))
    except Exception as exc:
        return Err(exc)


def _start_and_connect(
    cli_binary: str, name: str
) -> Result[tuple[str, str], Exception]:
    try:
        start_result = start_machine(cli_binary, name)
        if start_result.is_err():
            return Err(start_result.unwrap_err())
        ready_result = wait_until_ready(cli_binary)
        if ready_result.is_err():
            return Err(ready_result.unwrap_err())
        return _inspect_and_connect(cli_binary, name)
    except Exception as exc:
        return Err(exc)


def ensure_machine_ready(
    cli_binary: str, *, auto_start: bool, auto_init: bool
) -> Result[tuple[str, str], Exception]:
    """Detect a Podman machine, auto-start it if stopped (when auto_start),
    refuse to auto-create one unless auto_init is set, and resolve its
    connection URL. Returns (machine_name, connection_url)."""
    try:
        machines_result = list_machines(cli_binary)
        if machines_result.is_err():
            return Err(machines_result.unwrap_err())
        machines = machines_result.unwrap()

        if not machines:
            if not auto_init:
                return Err(
                    RuntimeError(
                        "No Podman machine found. Run 'podman machine init' first, "
                        "or set DVT_PODMAN_MACHINE_AUTO_INIT=true."
                    )
                )
            init_result = init_machine(cli_binary, _DEFAULT_MACHINE_NAME)
            if init_result.is_err():
                return Err(init_result.unwrap_err())
            return _start_and_connect(cli_binary, _DEFAULT_MACHINE_NAME)

        first_machine = machines[0]
        if not isinstance(first_machine, dict) or not isinstance(
            first_machine.get("Name"), str
        ):
            return Err(
                ValueError(
                    f"podman machine list returned unexpected shape: {first_machine!r}"
                )
            )
        name = first_machine["Name"].rstrip("*")
        inspect_result = inspect_machine(cli_binary, name)
        if inspect_result.is_err():
            return Err(inspect_result.unwrap_err())
        inspected = inspect_result.unwrap()

        if inspected.get("State") == "running":
            url_result = _connection_url(inspected)
            if url_result.is_err():
                return Err(url_result.unwrap_err())
            return Ok((name, url_result.unwrap()))

        if not auto_start:
            return Err(
                RuntimeError(
                    f"Machine {name!r} is not running. Run 'podman machine start {name}' first, "
                    "or set DVT_PODMAN_MACHINE_AUTO_START=true."
                )
            )
        return _start_and_connect(cli_binary, name)
    except Exception as exc:
        return Err(exc)
