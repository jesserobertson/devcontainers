from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, cast

import httpx
from docker.models.containers import Container
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate import podman_machine
from devtemplate.build import build_image
from devtemplate.config import Settings
from devtemplate.container import (
    config_has_drifted,
    find_workspace_container,
    refuse_unsupported,
    run_container,
    run_lifecycle_commands,
)
from devtemplate.features import pull_feature
from devtemplate.runtime import RuntimeHandle
from devtemplate.workspace.existing import (
    config_drift_error,
    folder_mismatch_error,
    rebuild_teardown,
    refresh_ssh_config,
    resume_existing,
)

__all__ = ["up_workspace"]


def feature_id(ref: str) -> str:
    """Derive a short id from an OCI ref's trailing path segment, e.g.
    'ghcr.io/jesserobertson/devcontainers/fastapi:latest' -> 'fastapi'. Used only
    for Dockerfile stage naming, not read from the Feature's own
    devcontainer-feature.json "id" field - an acceptable v1 simplification since
    this repo's own Features always keep the two in sync by construction."""
    return ref.rsplit("/", 1)[-1].split(":")[0]


@wrap_result
def load_config(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"{config_file} not found. Run 'dvt init' first.")
    return cast(dict[str, Any], json.loads(config_file.read_text()))


def image_tag(name: str) -> str:
    """The image tag dvt builds and tags a workspace's image under. Factored
    out so the tag literal used to remove the cached image (rebuild_teardown)
    and the one used to build the fresh image (build_image) can't drift apart."""
    return f"dvt/{name}:latest"


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
    against `project_path` before any of this, but the two branches need
    different levels of confidence in that check:

    - Without `--rebuild`, a *confirmed* mismatch (label present and
      different) skips the drift check entirely and just resumes - dvt can't
      meaningfully evaluate drift from the wrong vantage point. A *missing*
      label (foreign/pre-feature container, nothing to check against) falls
      back to the normal drift-check behavior below - resuming is
      non-destructive either way, so the lenient reading costs nothing.
    - With `--rebuild`, anything short of an *affirmatively confirmed* match
      (label present and equal) refuses outright, treating "missing label"
      the same as "confirmed mismatch": proceeding would tear down the
      existing container on nothing more than the assumption it belongs to
      `project_path`, which is exactly the hazard this check exists to close.
    """
    existing = find_workspace_container(handle.client, name)
    config_file = project_path / ".devcontainer" / "devcontainer.json"

    if existing is not None:
        existing_folder = existing.labels.get("devcontainer.local_folder")
        resolved_project_path = str(project_path.resolve())

        if not rebuild:
            # Lenient: a missing label isn't treated as a mismatch, since
            # resuming an unconfirmed container is non-destructive.
            folder_confirmed_mismatch = (
                existing_folder is not None and existing_folder != resolved_project_path
            )
            if not folder_confirmed_mismatch:
                config_result = load_config(config_file)
                if config_result.is_ok():
                    current_config = config_result.unwrap()
                    if config_has_drifted(existing, current_config):
                        raise config_drift_error(existing, current_config, name)
            return resume_existing(existing, name).unwrap()

        # Strict: --rebuild tears the container down, so it requires an
        # affirmatively confirmed match, not merely "not confirmed to differ".
        folder_confirmed_match = existing_folder == resolved_project_path
        if not folder_confirmed_match:
            raise folder_mismatch_error(existing_folder, project_path, name)

    config = load_config(config_file).unwrap()

    refuse_unsupported(config).unwrap()

    if "image" not in config:
        raise ValueError(
            f'{config_file} has no top-level "image" - only image-based '
            "devcontainer.json is supported"
        )

    if existing is not None:
        rebuild_teardown(handle.client, existing, image_tag(name)).unwrap()

    features_config = config.get("features", {})
    feature_refs = list(features_config.keys())

    with httpx.Client() as http_client:
        pulled = traverse_result(
            feature_refs,
            lambda ref: pull_feature(http_client, ref, settings.features_dir),
        ).unwrap()

    features = [
        (feature_id(ref), extracted_dir, features_config[ref])
        for ref, extracted_dir in zip(feature_refs, pulled, strict=True)
    ]

    if handle.machine_name is not None and "--gpus" in config.get("runArgs", []):
        podman_machine.ensure_gpu_support(
            handle.cli_binary, handle.machine_name
        ).unwrap()

    with tempfile.TemporaryDirectory() as scratch:
        image_tag_value = build_image(
            handle.client,
            config["image"],
            features,
            image_tag(name),
            Path(scratch),
            nocache=rebuild,
            pull=rebuild,
        ).unwrap()

    container = run_container(
        handle.client, image_tag_value, config, name, project_path, config_file
    ).unwrap()

    run_lifecycle_commands(container, config).unwrap()

    refresh_ssh_config(name).unwrap()

    return container
