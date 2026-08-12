from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, cast

import docker.types
from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Err, Ok, Result
from logerr.utilities import wrap_result

UNSUPPORTED_LIFECYCLE_FIELDS = {
    "onCreateCommand",
    "updateContentCommand",
    "initializeCommand",
    "postAttachCommand",
}
SUPPORTED_LIFECYCLE_ORDER = ["postCreateCommand", "postStartCommand"]


@wrap_result
def refuse_unsupported(config: dict[str, Any]) -> Result[None, Exception]:
    """Refuse (Err, nothing built) if config uses spec surface this runtime
    doesn't implement: docker-compose, build.dockerfile, lifecycle commands other
    than postCreateCommand/postStartCommand, or per-Feature installsAfter/
    dependsOn. See the design spec's Non-Goals for why each is out for v1.

    Examples:
        >>> refuse_unsupported({"image": "python:3.12"}).is_ok()
        True

        >>> refuse_unsupported({"dockerComposeFile": "docker-compose.yml"}).is_err()
        True
    """
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


def _encode_metadata(config: dict[str, Any]) -> str:
    """base64(json.dumps(config)) - the exact devcontainer.metadata label value.
    Factored out of compute_labels so read_stored_config's decode side and this
    encode side stay obviously in sync."""
    return base64.b64encode(json.dumps(config).encode()).decode()


def compute_labels(
    config: dict[str, Any], name: str, project_path: Path, config_file: Path
) -> dict[str, str]:
    """The label contract other devcontainer-aware tooling (VS Code's Dev
    Containers extension, @devcontainers/cli, devpod) uses to recognize and
    introspect a container dvt built."""
    return {
        "devcontainer.metadata": _encode_metadata(config),
        "devcontainer.local_folder": str(project_path.resolve()),
        "devcontainer.config_file": str(config_file.resolve()),
        "dvt.workspace": name,
    }


@wrap_result
def read_stored_config(container: Container) -> dict[str, Any]:
    """Decode a container's devcontainer.metadata label back into the dict it
    was built from. Errs if the label is missing or isn't valid base64/JSON -
    every container dvt itself builds always carries a well-formed one via
    compute_labels, so a failure here means a foreign or corrupted container."""
    encoded = container.labels.get("devcontainer.metadata")
    if encoded is None:
        raise ValueError("container has no devcontainer.metadata label")
    return cast(dict[str, Any], json.loads(base64.b64decode(encoded).decode()))


def config_has_drifted(container: Container, config: dict[str, Any]) -> bool:
    """True if container's stored config differs from config (the current
    on-disk devcontainer.json, already parsed). Dict equality, not label-string
    equality - JSON key order isn't meaningful. An unreadable stored config
    counts as drifted: better to ask for --rebuild than silently resume a
    container whose provenance can't be verified."""
    return (
        read_stored_config(container).map(lambda stored: stored != config).unwrap_or(True)
    )


def _substitute_mount_variables(
    mount_spec: str, project_path: Path, workspace_folder: str
) -> str:
    """Expand the devcontainer.json variables mount specs use in practice:
    ${localWorkspaceFolder} (the project's own absolute path, as written by
    this repo's own templates' workspaceMount) and ${containerWorkspaceFolder}
    (the resolved workspaceFolder). Left unsubstituted, a mount source of
    literally '${localWorkspaceFolder}' doesn't look like a host path to
    Docker/Podman, so it's misread as a named-volume name and rejected."""
    return mount_spec.replace(
        "${localWorkspaceFolder}", str(project_path.resolve())
    ).replace("${containerWorkspaceFolder}", workspace_folder)


def _parse_mount(mount_spec: str) -> dict[str, dict[str, str]]:
    """Parse a devcontainer.json mount string ('source=...,target=...,type=...')
    into docker-py's {source: {"bind": target, "mode": "rw"}} volumes form."""
    parts = dict(item.split("=", 1) for item in mount_spec.split(",") if "=" in item)
    return {parts["source"]: {"bind": parts["target"], "mode": "rw"}}


@wrap_result
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


# The devcontainer spec's `overrideCommand` defaults to true: the tool is expected
# to replace the image's own entrypoint/CMD with something that just keeps the
# container running, since `dvt ssh`/exec (not the image's foreground process) is
# how commands actually get run inside it. Without this, images whose default CMD
# doesn't block forever (a bare shell, most non-devcontainer base images) exit
# immediately after `docker run -d`, before anything can exec into them.
_KEEP_ALIVE_ENTRYPOINT = ["sleep", "infinity"]


@wrap_result
def run_container(
    client: DockerClient,
    image: str,
    config: dict[str, Any],
    name: str,
    project_path: Path,
    config_file: Path,
) -> Container:
    workspace_folder, workspace_mount = resolve_workspace(config, project_path)
    volumes: dict[str, dict[str, str]] = {}
    for mount_spec in [workspace_mount, *config.get("mounts", [])]:
        resolved_spec = _substitute_mount_variables(
            mount_spec, project_path, workspace_folder
        )
        volumes.update(_parse_mount(resolved_spec))

    cap_adds, device_requests = _translate_run_args(config.get("runArgs", [])).unwrap()

    entrypoint = _KEEP_ALIVE_ENTRYPOINT if config.get("overrideCommand", True) else None

    return client.containers.run(
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


@wrap_result
def run_lifecycle_commands(container: Container, config: dict[str, Any]) -> None:
    for field in SUPPORTED_LIFECYCLE_ORDER:
        command = config.get(field)
        if command is None:
            continue
        shell_command = command if isinstance(command, str) else " && ".join(command)
        exit_code, output = container.exec_run(["sh", "-c", shell_command])
        if exit_code != 0:
            output_text = (
                output.decode(errors="replace")
                if isinstance(output, bytes)
                else b"".join(output).decode(errors="replace")
            )
            raise RuntimeError(f"{field} failed (exit {exit_code}): {output_text}")


def find_workspace_container(client: DockerClient, name: str) -> Container | None:
    """Find the container tagged dvt.workspace=name - the sole source of truth for
    workspace lookup (no separate dvt-side registry; see the design spec)."""
    containers = client.containers.list(
        all=True, filters={"label": f"dvt.workspace={name}"}
    )
    return containers[0] if containers else None


def find_workspace_containers_by_folder(
    client: DockerClient, folder: Path
) -> list[Container]:
    """Find every container tagged devcontainer.local_folder=folder (resolved to an
    absolute path the same way compute_labels wrote it), regardless of its own
    dvt.workspace name - lets a caller recognize a workspace tied to this folder
    even if it was created under a name that doesn't match the folder's own."""
    return client.containers.list(
        all=True,
        filters={"label": f"devcontainer.local_folder={folder.resolve()}"},
    )
