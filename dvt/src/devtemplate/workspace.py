from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
from docker.client import DockerClient
from docker.models.containers import Container
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate import podman_machine
from devtemplate.build import build_image
from devtemplate.config import Settings
from devtemplate.container import (
    config_has_drifted,
    find_workspace_container,
    read_stored_config,
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


def _config_drift_error(
    existing: Container, config: dict[str, Any], name: str
) -> Exception:
    """Build the Err raised when an existing workspace's container doesn't
    match the current devcontainer.json. Distinguishes "config on disk
    differs from what was built" (lists the changed top-level keys) from
    "can't tell" (the container's own devcontainer.metadata label is
    unreadable) - both point at --rebuild, but the message says why."""
    stored_result = read_stored_config(existing)
    if stored_result.is_err():
        return ValueError(
            f"Workspace {name!r} already exists but dvt couldn't verify its "
            f"config ({stored_result.unwrap_err()}). Run 'dvt up --rebuild' "
            "to rebuild it."
        )
    stored = stored_result.unwrap()
    changed_keys = sorted(
        key
        for key in stored.keys() | config.keys()
        if stored.get(key) != config.get(key)
    )
    return ValueError(
        f"Workspace {name!r} already exists but its devcontainer.json has "
        f"changed since it was built ({', '.join(changed_keys)}). Run "
        "'dvt up --rebuild' to rebuild it, or revert devcontainer.json and "
        f"run 'dvt up' again. To use the existing container without going "
        f"through 'up' at all, run 'dvt ssh {name}'."
    )


def _folder_mismatch_error(
    existing_folder: str, project_path: Path, name: str
) -> Exception:
    """Build the Err raised when --rebuild is invoked for a workspace whose
    devcontainer.local_folder label points somewhere other than the folder
    dvt is currently running from. Rebuilding from the wrong vantage point
    would tear down the real workspace and rebuild it with an unrelated
    project's config, so this refuses outright and leaves the container
    completely untouched."""
    return ValueError(
        f"Workspace {name!r} was built from {existing_folder!r}, but dvt is "
        f"running from '{project_path.resolve()}'. Run 'dvt up --rebuild' "
        "from the workspace's own folder instead."
    )


def _image_tag(name: str) -> str:
    """The image tag dvt builds and tags a workspace's image under. Factored
    out so the tag literal used to remove the cached image (_rebuild_teardown)
    and the one used to build the fresh image (build_image) can't drift apart."""
    return f"dvt/{name}:latest"


@wrap_result
def _rebuild_teardown(
    client: DockerClient, existing: Container, image_tag: str
) -> None:
    """Remove the existing container so the fresh-build path below can run as
    if no workspace existed yet. Only existing.remove() failing is fatal
    (surfaced as Err) - if the old container can't be removed, --rebuild
    can't safely proceed. Dropping the cached image tag afterward is
    best-effort and swallowed on failure: it's purely for `docker images`
    hygiene, since the upcoming build_image(nocache=True, pull=True) call
    overwrites the tag regardless and is what actually forces freshness, not
    this removal.
    """
    existing.remove(force=True)
    try:
        client.images.remove(image_tag, force=True)
    except Exception:
        pass


@wrap_result
def up_workspace(
    handle: RuntimeHandle,
    settings: Settings,
    name: str,
    project_path: Path,
    rebuild: bool = False,
) -> Container:
    """Full `dvt up` sequence: validate -> pull Features -> build -> run ->
    lifecycle commands -> SSH config. Returns the running Container.

    Handles the re-`up` case (a workspace with this name already exists): if
    devcontainer.json is unreadable, or matches what the container was built
    from (compared via its devcontainer.metadata label), resumes it exactly
    as before. If devcontainer.json differs and `rebuild` is False, refuses
    with a message naming the changed keys and pointing at `--rebuild`. If
    `rebuild` is True, the config is loaded and validated *before* anything
    is torn down - only once that succeeds does dvt remove the existing
    container and its cached image tag (regardless of whether config
    actually drifted - `--rebuild` is also the general force-fresh escape
    hatch for e.g. a moved upstream base image tag), then falls through into
    the same build-from-scratch sequence used when no container exists yet,
    with Docker's build cache and base-image reuse both disabled.

    The existing container's `devcontainer.local_folder` label is checked
    against `project_path` before any of this: if it doesn't match (this
    `name` was resolved from a container built from a different folder), dvt
    can't meaningfully evaluate drift from here. Without `--rebuild` it just
    resumes, skipping the drift check entirely. With `--rebuild` it refuses
    outright instead - proceeding would tear down the *real* workspace and
    rebuild it using the wrong folder's devcontainer.json. A missing
    `devcontainer.local_folder` label (foreign/pre-feature container) is
    treated the same as a match, falling back to the normal drift-check
    behavior below.
    """
    existing = find_workspace_container(handle.client, name)
    config_file = project_path / ".devcontainer" / "devcontainer.json"

    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        folder_matches = existing_folder is None or existing_folder == str(
            project_path.resolve()
        )

        if not rebuild:
            if folder_matches:
                config_result = _load_config(config_file)
                if config_result.is_ok():
                    current_config = config_result.unwrap()
                    if config_has_drifted(existing, current_config):
                        raise _config_drift_error(existing, current_config, name)
            return _resume_existing(existing, name).unwrap()

        if not folder_matches:
            raise _folder_mismatch_error(existing_folder, project_path, name)

    config = _load_config(config_file).unwrap()

    refuse_unsupported(config).unwrap()

    if "image" not in config:
        raise ValueError(
            f'{config_file} has no top-level "image" - only image-based '
            "devcontainer.json is supported"
        )

    if existing is not None:
        _rebuild_teardown(handle.client, existing, _image_tag(name)).unwrap()

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
            _image_tag(name),
            Path(scratch),
            nocache=rebuild,
            pull=rebuild,
        ).unwrap()

    container = run_container(
        handle.client, image_tag, config, name, project_path, config_file
    ).unwrap()

    run_lifecycle_commands(container, config).unwrap()

    _refresh_ssh_config(name).unwrap()

    return container
