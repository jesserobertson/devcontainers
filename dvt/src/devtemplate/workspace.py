from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
from docker.models.containers import Container
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

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


@wrap_result
def _load_config(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"{config_file} not found. Run 'dvt init' first.")
    return cast(dict[str, Any], json.loads(config_file.read_text()))


def _refresh_ssh_config(name: str) -> Result[None, Exception]:
    return write_ssh_config_entry(name, Path.home() / ".ssh" / "config")


@wrap_result
def _resume_existing(existing: Container, name: str) -> Container:
    """Handle the re-`up` case where a container already carries this
    workspace's label: start it if it isn't running, then (re)write its SSH
    config entry. Every fallible operation on `existing` - status access and
    start(), both of which can raise docker-py's APIError - is wrapped, so
    nothing escapes as a bare exception.
    """
    if existing.status != "running":
        existing.start()
    _refresh_ssh_config(name).unwrap()
    return existing


@wrap_result
def up_workspace(
    handle: RuntimeHandle, settings: Settings, name: str, project_path: Path
) -> Container:
    """Full `dvt up` sequence: validate -> pull Features -> build -> run ->
    lifecycle commands -> SSH config. Returns the running Container.

    Handles the re-`up` case (a workspace with this name already exists): if its
    container is stopped, starts it rather than rebuilding; if already running,
    just ensures the SSH config entry is current and returns it. Only builds+runs
    from scratch when no container with this `dvt.workspace` label exists yet -
    no in-place devcontainer.json changes are picked up on re-`up` in v1; delete
    and re-`up` for that.
    """
    existing = find_workspace_container(handle.client, name)
    if existing is not None:
        return _resume_existing(existing, name).unwrap()

    config_file = project_path / ".devcontainer" / "devcontainer.json"
    config = _load_config(config_file).unwrap()

    refuse_unsupported(config).unwrap()

    if "image" not in config:
        raise ValueError(
            f'{config_file} has no top-level "image" - only image-based '
            "devcontainer.json is supported"
        )

    features_config = config.get("features", {})
    feature_refs = list(features_config.keys())

    with httpx.Client() as http_client:
        pulled = traverse_result(
            feature_refs,
            lambda ref: pull_feature(http_client, ref, settings.data_dir / "features"),
        ).unwrap()

    features = [
        (_feature_id(ref), extracted_dir, features_config[ref])
        for ref, extracted_dir in zip(feature_refs, pulled, strict=True)
    ]

    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        podman_machine.ensure_gpu_support(
            handle.cli_binary, handle.machine_name
        ).unwrap()

    with tempfile.TemporaryDirectory() as scratch:
        image_tag = build_image(
            handle.client,
            config["image"],
            features,
            f"dvt/{name}:latest",
            Path(scratch),
        ).unwrap()

    container = run_container(
        handle.client, image_tag, config, name, project_path, config_file
    ).unwrap()

    run_lifecycle_commands(container, config).unwrap()

    _refresh_ssh_config(name).unwrap()

    return container
