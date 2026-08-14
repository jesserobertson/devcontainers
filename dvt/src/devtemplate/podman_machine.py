from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Any, cast

from logerr import Ok, Result
from logerr.utilities import wrap_result

DEFAULT_MACHINE_NAME = "dvt-machine"
DEFAULT_CPUS = 2
DEFAULT_MEMORY_MB = 4096
DEFAULT_DISK_GB = 100
NAMED_PIPE_PATTERN = re.compile(r"\\\\\.\\pipe\\(.+)$")

INIT_TIMEOUT_SECONDS = 600
"""`podman machine init` downloads and unpacks a WSL VM image on first run.
300s was a plausible-sounding round number that a genuinely slow link can
exceed, and exceeding it leaves a half-created machine behind plus an opaque
`TimeoutExpired` - the worst of both outcomes. 600s is a more realistic floor
for a cold image pull; it is still a bound, just one a working setup won't
trip."""

__all__ = [
    "list_machines",
    "inspect_machine",
    "start_machine",
    "init_machine",
    "wait_until_ready",
    "check_gpu_cdi_ready",
    "install_nvidia_toolkit",
    "ensure_gpu_support",
    "ensure_machine_ready",
]


def announce(message: str) -> None:
    """Tell the user a multi-minute blocking operation has started.

    On stderr, and via plain `print` rather than a `rich` Console: this module
    is otherwise pure Result-returning side-effect-isolated logic with no UI
    dependency at all, and nothing else outside `cli.py`/`commands/` in this
    codebase constructs a Console (`devtemplate.sshd.server` sets the same precedent for
    a non-CLI module needing to say something). stderr keeps it clear of any
    stdout a caller may be treating as data.

    Without this, composed operations - `dvt up` on a first GPU run does
    init, then start, then the NVIDIA toolkit install - are up to ten minutes
    of complete silence, indistinguishable from a hang, because every one of
    these subprocess calls captures its output.
    """
    print(message, file=sys.stderr, flush=True)


@wrap_result
def run_podman_json(cli_binary: str, args: list[str]) -> Any:
    result = subprocess.run(
        [cli_binary, *args], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"podman {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def list_machines(cli_binary: str) -> Result[list[dict[str, Any]], Exception]:
    return cast(
        "Result[list[dict[str, Any]], Exception]",
        run_podman_json(cli_binary, ["machine", "list", "--format", "json"]),
    )


@wrap_result
def inspect_machine(cli_binary: str, name: str) -> dict[str, Any]:
    inspected = run_podman_json(cli_binary, ["machine", "inspect", name]).unwrap()
    if (
        not isinstance(inspected, list)
        or not inspected
        or not isinstance(inspected[0], dict)
    ):
        raise ValueError(
            f"podman machine inspect {name!r} returned unexpected shape: {inspected!r}"
        )
    return inspected[0]


@wrap_result
def start_machine(cli_binary: str, name: str) -> None:
    announce(f"Starting Podman machine {name!r} (this can take a minute)...")
    result = subprocess.run(
        [cli_binary, "machine", "start", name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to start machine {name!r}: {result.stderr.strip()}")


@wrap_result
def init_machine(cli_binary: str, name: str) -> None:
    announce(
        f"Initializing Podman machine {name!r} - this downloads a VM image "
        "and may take several minutes on first run..."
    )
    result = subprocess.run(
        [
            cli_binary,
            "machine",
            "init",
            name,
            "--cpus",
            str(DEFAULT_CPUS),
            "--memory",
            str(DEFAULT_MEMORY_MB),
            "--disk-size",
            str(DEFAULT_DISK_GB),
        ],
        capture_output=True,
        text=True,
        timeout=INIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to init machine {name!r}: {result.stderr.strip()}")


@wrap_result
def wait_until_ready(cli_binary: str, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = subprocess.run(
            [cli_binary, "ps"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError(f"Machine did not become ready within {timeout_seconds}s")


@wrap_result
def connection_url(inspected: dict[str, Any]) -> str:
    """Translate `podman machine inspect`'s ConnectionInfo into a docker-py
    base_url. Verified directly against a real machine: PodmanPipe.Path looks
    like '\\\\.\\pipe\\podman-devpod-machine' on Windows; docker-py expects
    'npipe:////./pipe/podman-devpod-machine'. Falls back to PodmanSocket for
    non-Windows callers of this function (WSL-internal use), though this
    module is only invoked from runtime.py on win32 - see Global Constraints."""
    connection_info = inspected.get("ConnectionInfo")
    if not isinstance(connection_info, dict):
        raise ValueError(f"machine inspect result has no ConnectionInfo: {inspected!r}")
    pipe = connection_info.get("PodmanPipe")
    if isinstance(pipe, dict) and isinstance(pipe.get("Path"), str):
        match = NAMED_PIPE_PATTERN.match(pipe["Path"])
        if match:
            return f"npipe:////./pipe/{match.group(1)}"
    socket_info = connection_info.get("PodmanSocket")
    if isinstance(socket_info, dict) and isinstance(socket_info.get("Path"), str):
        return f"unix://{socket_info['Path']}"
    raise ValueError(
        f"machine inspect result has no usable connection endpoint: {connection_info!r}"
    )


@wrap_result
def inspect_and_connect(cli_binary: str, name: str) -> tuple[str, str]:
    inspected = inspect_machine(cli_binary, name).unwrap()
    url = connection_url(inspected).unwrap()
    return name, url


@wrap_result
def start_and_connect(cli_binary: str, name: str) -> Result[tuple[str, str], Exception]:
    start_machine(cli_binary, name).unwrap()
    wait_until_ready(cli_binary).unwrap()
    return inspect_and_connect(cli_binary, name)


NVIDIA_TOOLKIT_INSTALL_COMMAND = (
    "sudo curl -s -L https://nvidia.github.io/libnvidia-container/stable/rpm/"
    "nvidia-container-toolkit.repo -o /etc/yum.repos.d/nvidia-container-toolkit.repo "
    "&& sudo dnf install -y nvidia-container-toolkit "
    "&& sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
)


@wrap_result
def check_gpu_cdi_ready(cli_binary: str, machine_name: str) -> bool:
    result = subprocess.run(
        [
            cli_binary,
            "machine",
            "ssh",
            machine_name,
            "test -f /etc/cdi/nvidia.yaml && echo exists || echo missing",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to check CDI status on {machine_name!r}: {result.stderr.strip()}"
        )
    return "exists" in result.stdout


@wrap_result
def install_nvidia_toolkit(cli_binary: str, machine_name: str) -> None:
    announce(
        "Installing the NVIDIA Container Toolkit in Podman machine "
        f"{machine_name!r} - this may take several minutes..."
    )
    result = subprocess.run(
        [cli_binary, "machine", "ssh", machine_name, NVIDIA_TOOLKIT_INSTALL_COMMAND],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to install NVIDIA Container Toolkit on {machine_name!r}: "
            f"{result.stderr.strip()}"
        )


@wrap_result
def ensure_gpu_support(cli_binary: str, machine_name: str) -> Result[None, Exception]:
    if check_gpu_cdi_ready(cli_binary, machine_name).unwrap():
        return Ok(None)
    return install_nvidia_toolkit(cli_binary, machine_name)


@wrap_result
def ensure_machine_ready(
    cli_binary: str, *, auto_start: bool, auto_init: bool
) -> Result[tuple[str, str], Exception]:
    """Detect a Podman machine, auto-start it if stopped (when auto_start),
    refuse to auto-create one unless auto_init is set, and resolve its
    connection URL. Returns (machine_name, connection_url)."""
    machines = list_machines(cli_binary).unwrap()

    if not machines:
        if not auto_init:
            raise RuntimeError(
                "No Podman machine found. Run 'podman machine init' first, "
                "or set DVT_PODMAN_MACHINE_AUTO_INIT=true."
            )
        init_machine(cli_binary, DEFAULT_MACHINE_NAME).unwrap()
        if not auto_start:
            raise RuntimeError(
                f"Machine {DEFAULT_MACHINE_NAME!r} is not running. "
                f"Run 'podman machine start {DEFAULT_MACHINE_NAME}' first, "
                "or set DVT_PODMAN_MACHINE_AUTO_START=true."
            )
        return start_and_connect(cli_binary, DEFAULT_MACHINE_NAME)

    first_machine = machines[0]
    if not isinstance(first_machine, dict) or not isinstance(
        first_machine.get("Name"), str
    ):
        raise ValueError(
            f"podman machine list returned unexpected shape: {first_machine!r}"
        )
    name = first_machine["Name"].rstrip("*")
    inspected = inspect_machine(cli_binary, name).unwrap()

    if inspected.get("State") == "running":
        url = connection_url(inspected).unwrap()
        return Ok((name, url))

    if not auto_start:
        raise RuntimeError(
            f"Machine {name!r} is not running. Run 'podman machine start {name}' first, "
            "or set DVT_PODMAN_MACHINE_AUTO_START=true."
        )
    return start_and_connect(cli_binary, name)
