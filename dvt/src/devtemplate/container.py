from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import docker.types
from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Err, Ok, Result

UNSUPPORTED_LIFECYCLE_FIELDS = {
    "onCreateCommand",
    "updateContentCommand",
    "initializeCommand",
    "postAttachCommand",
}
SUPPORTED_LIFECYCLE_ORDER = ["postCreateCommand", "postStartCommand"]


def refuse_unsupported(config: dict[str, Any]) -> Result[None, Exception]:
    """Refuse (Err, nothing built) if config uses spec surface this runtime
    doesn't implement: docker-compose, build.dockerfile, lifecycle commands other
    than postCreateCommand/postStartCommand, or per-Feature installsAfter/
    dependsOn. See the design spec's Non-Goals for why each is out for v1."""
    try:
        if "dockerComposeFile" in config:
            return Err(ValueError("dockerComposeFile devcontainers are not supported"))
        if "build" in config:
            return Err(
                ValueError(
                    'build.dockerfile devcontainers are not supported - use "image" instead'
                )
            )
        used_unsupported = UNSUPPORTED_LIFECYCLE_FIELDS & config.keys()
        if used_unsupported:
            return Err(
                ValueError(
                    f"Unsupported lifecycle command(s): {sorted(used_unsupported)} "
                    "(only postCreateCommand/postStartCommand are supported)"
                )
            )
        for feature_ref, feature_options in config.get("features", {}).items():
            if isinstance(feature_options, dict) and (
                "installsAfter" in feature_options or "dependsOn" in feature_options
            ):
                return Err(
                    ValueError(
                        f"Feature {feature_ref!r} uses installsAfter/dependsOn, "
                        "which this runtime doesn't support (single-Feature only)"
                    )
                )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def resolve_workspace(config: dict[str, Any], project_path: Path) -> tuple[str, str]:
    """Returns (workspace_folder, workspace_mount_spec), applying the spec's
    /workspaces/<folder-name> default when devcontainer.json doesn't set them."""
    default_folder = f"/workspaces/{project_path.resolve().name}"
    workspace_folder = config.get("workspaceFolder", default_folder)
    workspace_mount = config.get(
        "workspaceMount",
        f"source={project_path.resolve()},target={workspace_folder},type=bind,consistency=cached",
    )
    return workspace_folder, workspace_mount


def compute_labels(
    config: dict[str, Any], name: str, project_path: Path, config_file: Path
) -> dict[str, str]:
    """The label contract other devcontainer-aware tooling (VS Code's Dev
    Containers extension, @devcontainers/cli, devpod) uses to recognize and
    introspect a container dvt built."""
    metadata_json = json.dumps(config)
    return {
        "devcontainer.metadata": base64.b64encode(metadata_json.encode()).decode(),
        "devcontainer.local_folder": str(project_path.resolve()),
        "devcontainer.config_file": str(config_file.resolve()),
        "dvt.workspace": name,
    }


def _parse_mount(mount_spec: str) -> dict[str, dict[str, str]]:
    """Parse a devcontainer.json mount string ('source=...,target=...,type=...')
    into docker-py's {source: {"bind": target, "mode": "rw"}} volumes form."""
    parts = dict(item.split("=", 1) for item in mount_spec.split(",") if "=" in item)
    return {parts["source"]: {"bind": parts["target"], "mode": "rw"}}


def _translate_run_args(
    run_args: list[str],
) -> Result[tuple[list[str], list[Any]], Exception]:
    """Translate devcontainer.json's runArgs into (cap_add list, device_requests
    list) for docker-py's containers.run(). Only --cap-add=X and the two-element
    ["--gpus", "all"] form are recognized (the only shapes this repo's templates
    use) - any other flag is refused rather than silently dropped."""
    cap_adds: list[str] = []
    device_requests: list[Any] = []
    index = 0
    try:
        while index < len(run_args):
            arg = run_args[index]
            if arg.startswith("--cap-add="):
                cap_adds.append(arg.split("=", 1)[1])
                index += 1
            elif (
                arg == "--gpus"
                and index + 1 < len(run_args)
                and run_args[index + 1] == "all"
            ):
                device_requests.append(
                    docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])
                )
                index += 2
            else:
                return Err(ValueError(f"Unsupported runArgs entry {arg!r}"))
        return Ok((cap_adds, device_requests))
    except Exception as exc:
        return Err(exc)


# The devcontainer spec's `overrideCommand` defaults to true: the tool is expected
# to replace the image's own entrypoint/CMD with something that just keeps the
# container running, since `dvt ssh`/exec (not the image's foreground process) is
# how commands actually get run inside it. Without this, images whose default CMD
# doesn't block forever (a bare shell, most non-devcontainer base images) exit
# immediately after `docker run -d`, before anything can exec into them.
_KEEP_ALIVE_ENTRYPOINT = ["sleep", "infinity"]


def run_container(
    client: DockerClient,
    image: str,
    config: dict[str, Any],
    name: str,
    project_path: Path,
    config_file: Path,
) -> Result[Container, Exception]:
    try:
        workspace_folder, workspace_mount = resolve_workspace(config, project_path)
        volumes: dict[str, dict[str, str]] = {}
        for mount_spec in [workspace_mount, *config.get("mounts", [])]:
            volumes.update(_parse_mount(mount_spec))

        run_args_result = _translate_run_args(config.get("runArgs", []))
        if run_args_result.is_err():
            return Err(run_args_result.unwrap_err())
        cap_adds, device_requests = run_args_result.unwrap()

        entrypoint = (
            _KEEP_ALIVE_ENTRYPOINT if config.get("overrideCommand", True) else None
        )

        container = client.containers.run(
            image,
            detach=True,
            name=f"dvt-{name}",
            labels=compute_labels(config, name, project_path, config_file),
            volumes=volumes,
            working_dir=workspace_folder,
            environment=config.get("containerEnv", {}),
            user=config.get("remoteUser"),
            cap_add=cap_adds,
            device_requests=device_requests,
            entrypoint=entrypoint,
        )
        return Ok(container)
    except Exception as exc:
        return Err(exc)


def run_lifecycle_commands(
    container: Container, config: dict[str, Any]
) -> Result[None, Exception]:
    try:
        for field in SUPPORTED_LIFECYCLE_ORDER:
            command = config.get(field)
            if command is None:
                continue
            shell_command = (
                command if isinstance(command, str) else " && ".join(command)
            )
            exit_code, output = container.exec_run(["sh", "-c", shell_command])
            if exit_code != 0:
                output_text = (
                    output.decode(errors="replace")
                    if isinstance(output, bytes)
                    else b"".join(output).decode(errors="replace")
                )
                return Err(
                    RuntimeError(f"{field} failed (exit {exit_code}): {output_text}")
                )
        return Ok(None)
    except Exception as exc:
        return Err(exc)


def find_workspace_container(client: DockerClient, name: str) -> Container | None:
    """Find the container tagged dvt.workspace=name - the sole source of truth for
    workspace lookup (no separate dvt-side registry; see the design spec)."""
    containers = client.containers.list(
        all=True, filters={"label": f"dvt.workspace={name}"}
    )
    return containers[0] if containers else None
