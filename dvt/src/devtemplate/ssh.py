from __future__ import annotations

import subprocess
from pathlib import Path

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate import ssh_server
from devtemplate.container import find_workspace_container

_BEGIN_MARKER = "# BEGIN dvt {name}"
_END_MARKER = "# END dvt {name}"


def write_ssh_config_entry(name: str, ssh_config_path: Path) -> Result[None, Exception]:
    """Write/replace a `Host <name>` block whose ProxyCommand pipes through
    `dvt ssh --stdio <name>` - a real SSH server (ssh_server.py), not a bare
    shell. No sshd is ever installed into any image; the server runs entirely
    within the `dvt ssh --stdio` process itself."""
    try:
        removal = remove_ssh_config_entry(name, ssh_config_path)
        if removal.is_err():
            return removal
        block = (
            f"\n{_BEGIN_MARKER.format(name=name)}\n"
            f"Host {name}\n"
            f"    HostName {name}\n"
            f"    ProxyCommand dvt ssh --stdio {name}\n"
            f"    StrictHostKeyChecking no\n"
            f"    UserKnownHostsFile /dev/null\n"
            f"{_END_MARKER.format(name=name)}\n"
        )
        ssh_config_path.parent.mkdir(parents=True, exist_ok=True)
        with ssh_config_path.open("a") as f:
            f.write(block)
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def remove_ssh_config_entry(
    name: str, ssh_config_path: Path
) -> Result[None, Exception]:
    if not ssh_config_path.exists():
        return Ok(None)
    try:
        begin, end = _BEGIN_MARKER.format(name=name), _END_MARKER.format(name=name)
        kept: list[str] = []
        skipping = False
        for line in ssh_config_path.read_text().splitlines(keepends=True):
            if line.strip() == begin:
                skipping = True
                continue
            if line.strip() == end:
                skipping = False
                continue
            if not skipping:
                kept.append(line)
        ssh_config_path.write_text("".join(kept))
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def stdio_proxy(
    cli_binary: str, client: DockerClient, name: str
) -> Result[int, Exception]:
    """The non-interactive pipe mode `dvt ssh --stdio <name>` runs: finds the
    container labeled dvt.workspace=name and runs a real SSH server
    (ssh_server.run_stdio_server) bound to this process's own stdin/stdout,
    bridged to `docker/podman exec -i` in that container. This is what the
    ProxyCommand entry written by write_ssh_config_entry invokes."""
    try:
        container = find_workspace_container(client, name)
        if container is None or container.name is None:
            return Err(ValueError(f"No workspace named {name!r} is running."))
        return Ok(ssh_server.run_stdio_server(cli_binary, container.name))
    except Exception as exc:
        return Err(exc)


def exec_interactive(
    cli_binary: str, client: DockerClient, name: str
) -> Result[int, Exception]:
    """`dvt ssh <name>` typed directly at a terminal: finds the container labeled
    dvt.workspace=name and execs `docker exec -it` (inheriting this process's
    stdin/stdout/tty directly), returning its exit code. Unaffected by this
    plan - unlike stdio_proxy, this never involved SSH protocol at all."""
    try:
        container = find_workspace_container(client, name)
        if container is None or container.name is None:
            return Err(ValueError(f"No workspace named {name!r} is running."))
        result = subprocess.run([cli_binary, "exec", "-it", container.name, "sh"])
        return Ok(result.returncode)
    except Exception as exc:
        return Err(exc)
