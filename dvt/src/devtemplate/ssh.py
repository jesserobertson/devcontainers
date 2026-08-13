from __future__ import annotations

import subprocess
from pathlib import Path

from docker.client import DockerClient
from logerr.utilities import wrap_result

from devtemplate.container import find_workspace_container

_BEGIN_MARKER = "# BEGIN dvt {name}"
_END_MARKER = "# END dvt {name}"


@wrap_result
def write_ssh_config_entry(name: str, ssh_config_path: Path) -> None:
    """Write/replace a `Host <name>` block whose ProxyCommand pipes through
    `dvt ssh --stdio <name>` - a real SSH server (ssh_server.py), not a bare
    shell. No sshd is ever installed into any image; the server runs entirely
    within the `dvt ssh --stdio` process itself."""
    remove_ssh_config_entry(name, ssh_config_path).unwrap()
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


@wrap_result
def remove_ssh_config_entry(name: str, ssh_config_path: Path) -> None:
    if not ssh_config_path.exists():
        return
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


@wrap_result
def stdio_proxy(cli_binary: str, client: DockerClient, name: str) -> int:
    """The non-interactive pipe mode `dvt ssh --stdio <name>` runs: finds the
    container labeled dvt.workspace=name and runs a real SSH server
    (ssh_server.run_stdio_server) bound to this process's own stdin/stdout,
    bridged to `docker/podman exec` in that container - `-it` with a real
    host-side pty for sessions that requested one, `-i` otherwise (see
    ssh_server's own module docstring). This is what the ProxyCommand entry
    written by write_ssh_config_entry invokes."""
    # Imported here, not at module scope, and inside the try like every
    # other fallible statement in this codebase: ssh_server pulls in
    # asyncssh and transitively cryptography, ~25% of
    # `import devtemplate.cli`'s startup cost, and this is the only code
    # path in the whole CLI that ever needs it.
    from devtemplate import ssh_server

    container = find_workspace_container(client, name)
    if container is None or container.name is None:
        raise ValueError(f"No workspace named {name!r} is running.")
    return ssh_server.run_stdio_server(cli_binary, container.name)


@wrap_result
def exec_interactive(cli_binary: str, client: DockerClient, name: str) -> int:
    """`dvt ssh <name>` typed directly at a terminal: finds the container labeled
    dvt.workspace=name and execs `docker exec -it` (inheriting this process's
    stdin/stdout/tty directly), returning its exit code. Unaffected by this
    plan - unlike stdio_proxy, this never involved SSH protocol at all.

    Runs the container user's own configured shell (falling back to sh if
    $SHELL isn't set) rather than a hardcoded sh, so shell-startup hooks an
    image wires up (e.g. a pixi project's `pixi shell-hook` in .bashrc/fish's
    conf.d) actually fire - matching ssh_server.py's bare-shell-request path.
    """
    container = find_workspace_container(client, name)
    if container is None or container.name is None:
        raise ValueError(f"No workspace named {name!r} is running.")
    result = subprocess.run(
        [
            cli_binary,
            "exec",
            "-it",
            container.name,
            "sh",
            "-c",
            'exec "${SHELL:-sh}"',
        ]
    )
    return result.returncode
