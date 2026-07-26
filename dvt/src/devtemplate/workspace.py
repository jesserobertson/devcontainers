from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
from docker.models.containers import Container
from logerr import Err, Ok, Result
from logerr.itertools import traverse_result

from devtemplate import podman_machine
from devtemplate.build import build_image
from devtemplate.config import Settings
from devtemplate.container import (
    find_workspace_container,
    refuse_unsupported,
    run_container,
    run_lifecycle_commands,
)
from devtemplate.features import pull_feature
from devtemplate.runtime import RuntimeHandle
from devtemplate.ssh import write_ssh_config_entry


def _feature_id(ref: str) -> str:
    """Derive a short id from an OCI ref's trailing path segment, e.g.
    'ghcr.io/jesserobertson/devcontainers/fastapi:latest' -> 'fastapi'. Used only
    for Dockerfile stage naming, not read from the Feature's own
    devcontainer-feature.json "id" field - an acceptable v1 simplification since
    this repo's own Features always keep the two in sync by construction."""
    return ref.rsplit("/", 1)[-1].split(":")[0]


def _load_config(config_file: Path) -> Result[dict[str, Any], Exception]:
    if not config_file.exists():
        return Err(
            FileNotFoundError(f"{config_file} not found. Run 'dvt project init' first.")
        )
    try:
        return Ok(json.loads(config_file.read_text()))
    except Exception as exc:
        return Err(exc)


def _refresh_ssh_config(name: str) -> Result[None, Exception]:
    try:
        return write_ssh_config_entry(name, Path.home() / ".ssh" / "config")
    except Exception as exc:
        return Err(exc)


def _resume_existing(existing: Container, name: str) -> Result[Container, Exception]:
    """Handle the re-`up` case where a container already carries this
    workspace's label: start it if it isn't running, then (re)write its SSH
    config entry. Every fallible operation on `existing` - status access and
    start(), both of which can raise docker-py's APIError - is wrapped, so
    nothing escapes as a bare exception.
    """
    try:
        if existing.status != "running":
            existing.start()
    except Exception as exc:
        return Err(exc)
    ssh_result = _refresh_ssh_config(name)
    if ssh_result.is_err():
        return Err(ssh_result.unwrap_err())
    return Ok(existing)


def up_workspace(
    handle: RuntimeHandle, settings: Settings, name: str, project_path: Path
) -> Result[Container, Exception]:
    """Full `dvt up` sequence: validate -> pull Features -> build -> run ->
    lifecycle commands -> SSH config. Returns the running Container.

    Handles the re-`up` case (a workspace with this name already exists): if its
    container is stopped, starts it rather than rebuilding; if already running,
    just ensures the SSH config entry is current and returns it. Only builds+runs
    from scratch when no container with this `dvt.workspace` label exists yet -
    no in-place devcontainer.json changes are picked up on re-`up` in v1; delete
    and re-`up` for that.
    """
    try:
        existing = find_workspace_container(handle.client, name)
    except Exception as exc:
        return Err(exc)
    if existing is not None:
        return _resume_existing(existing, name)

    config_file = project_path / ".devcontainer" / "devcontainer.json"
    config_result = _load_config(config_file)
    if config_result.is_err():
        return Err(config_result.unwrap_err())
    config = config_result.unwrap()

    refusal = refuse_unsupported(config)
    if refusal.is_err():
        return Err(refusal.unwrap_err())

    if "image" not in config:
        return Err(
            ValueError(
                f'{config_file} has no top-level "image" - only image-based '
                "devcontainer.json is supported"
            )
        )

    try:
        features_config = config.get("features", {})
        feature_refs = list(features_config.keys())
    except Exception as exc:
        return Err(exc)

    try:
        with httpx.Client() as http_client:
            pulled_result = traverse_result(
                feature_refs,
                lambda ref: pull_feature(
                    http_client, ref, settings.data_dir / "features"
                ),
            )
    except Exception as exc:
        return Err(exc)
    if pulled_result.is_err():
        return Err(pulled_result.unwrap_err())

    try:
        features = [
            (_feature_id(ref), extracted_dir, features_config[ref])
            for ref, extracted_dir in zip(
                feature_refs, pulled_result.unwrap(), strict=True
            )
        ]
    except Exception as exc:
        return Err(exc)

    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        gpu_result = podman_machine.ensure_gpu_support(
            handle.cli_binary, handle.machine_name
        )
        if gpu_result.is_err():
            return Err(gpu_result.unwrap_err())

    try:
        with tempfile.TemporaryDirectory() as scratch:
            build_result = build_image(
                handle.client,
                config["image"],
                features,
                f"dvt/{name}:latest",
                Path(scratch),
            )
    except Exception as exc:
        return Err(exc)
    if build_result.is_err():
        return Err(build_result.unwrap_err())

    run_result = run_container(
        handle.client, build_result.unwrap(), config, name, project_path, config_file
    )
    if run_result.is_err():
        return Err(run_result.unwrap_err())
    container = run_result.unwrap()

    lifecycle_result = run_lifecycle_commands(container, config)
    if lifecycle_result.is_err():
        return Err(lifecycle_result.unwrap_err())

    ssh_result = _refresh_ssh_config(name)
    if ssh_result.is_err():
        return Err(ssh_result.unwrap_err())

    return Ok(container)
