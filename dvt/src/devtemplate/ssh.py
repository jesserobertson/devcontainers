from __future__ import annotations

import subprocess

from docker.client import DockerClient
from logerr import Err, Ok, Result

from devtemplate.container import find_workspace_container


def exec_interactive(
    cli_binary: str, client: DockerClient, name: str
) -> Result[int, Exception]:
    """`dvt ssh <name>` typed directly at a terminal: finds the container labeled
    dvt.workspace=name and execs `docker exec -it` (inheriting this process's
    stdin/stdout/tty directly), returning its exit code."""
    try:
        container = find_workspace_container(client, name)
        if container is None or container.name is None:
            return Err(ValueError(f"No workspace named {name!r} is running."))
        result = subprocess.run([cli_binary, "exec", "-it", container.name, "sh"])
        return Ok(result.returncode)
    except Exception as exc:
        return Err(exc)
