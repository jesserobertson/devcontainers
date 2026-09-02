from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from docker.client import DockerClient
from logerr.utilities import wrap_result

__all__ = ["generate_dockerfile", "build_image"]


def dockerfile_stage_name(index: int, feature_id: str) -> str:
    return f"feature-{index}-{feature_id}"


def generate_dockerfile(
    base_image: str,
    features: list[tuple[str, str, dict[str, str], dict[str, str]]],
) -> str:
    """Generate a multi-stage Dockerfile: base image, then one stage per Feature
    that COPYs in its extracted directory (already placed under the build context
    at the given context-relative dir by build_image), switches to USER root -
    the devcontainer Features spec requires install.sh to always run as root,
    regardless of what USER the base image (or a prior Feature stage) left
    active - emits the Feature's own containerEnv as plain Docker ENV
    instructions, and runs install.sh with its resolved options as env vars plus
    the spec's standard _REMOTE_USER/_CONTAINER_USER vars.

    features: list of (feature_id, context_relative_dir, resolved_options,
    container_env), already in resolved install order (see feature_graph).
    container_env is the Feature's own devcontainer-feature.json "containerEnv"
    ({} when absent); its values are emitted verbatim inside double quotes, so
    a ${VAR} reference is left for Docker/the shell to expand and is NOT
    quote-escaped.

    Examples:
        >>> print(generate_dockerfile(
        ...     "python:3.12",
        ...     [("fastapi", "features/0-fastapi", {"version": "1.0"},
        ...       {"PATH": "/opt/fastapi/bin:${PATH}"})],
        ... ))
        FROM python:3.12 AS stage0
        FROM stage0 AS feature-0-fastapi
        COPY features/0-fastapi/ /tmp/dvt-feature/
        USER root
        ENV PATH="/opt/fastapi/bin:${PATH}"
        RUN chmod +x /tmp/dvt-feature/install.sh && _REMOTE_USER=dev _CONTAINER_USER=dev VERSION=1.0 /tmp/dvt-feature/install.sh && rm -rf /tmp/dvt-feature
        FROM feature-0-fastapi AS final
        <BLANKLINE>
    """
    lines = [f"FROM {base_image} AS stage0"]
    current_stage = "stage0"
    for index, (feature_id, context_dir, options, container_env) in enumerate(features):
        stage_name = dockerfile_stage_name(index, feature_id)
        lines.append(f"FROM {current_stage} AS {stage_name}")
        lines.append(f"COPY {context_dir}/ /tmp/dvt-feature/")
        lines.append("USER root")
        for key in sorted(container_env):
            # trusted feature metadata, emitted verbatim - a literal `"` in a
            # value would break the line
            lines.append(f'ENV {key}="{container_env[key]}"')
        env_assignments = " ".join(
            f"{key.upper()}={shlex.quote(value)}" for key, value in options.items()
        )
        env_prefix = f"{env_assignments} " if env_assignments else ""
        lines.append(
            "RUN chmod +x /tmp/dvt-feature/install.sh && "
            f"_REMOTE_USER=dev _CONTAINER_USER=dev {env_prefix}"
            "/tmp/dvt-feature/install.sh && rm -rf /tmp/dvt-feature"
        )
        current_stage = stage_name
    lines.append(f"FROM {current_stage} AS final")
    return "\n".join(lines) + "\n"


@wrap_result
def build_image(
    client: DockerClient,
    base_image: str,
    features: list[tuple[str, Path, dict[str, str], dict[str, str]]],
    tag: str,
    scratch_dir: Path,
    *,
    nocache: bool = False,
    pull: bool = False,
) -> str:
    """Assemble a build context under scratch_dir (copying each extracted Feature
    directory in), write the generated Dockerfile, and build it. features: list of
    (feature_id, extracted_dir, resolved_options, container_env), already in
    resolved install order.

    nocache/pull default to False (normal cached build). Set both True to force
    a from-scratch rebuild (used by `dvt up --rebuild`): nocache disables
    Docker's build-layer cache, pull re-fetches the base image even if a local
    copy exists - together they pick up a moved upstream base image tag or a
    stale intermediate layer. Simply deleting the previously built image tag
    would not achieve this, since Docker's build cache is keyed by instruction
    content, not by output tag."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    context_features: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
    for index, (feature_id, extracted_dir, options, container_env) in enumerate(
        features
    ):
        context_relative = f"features/{index}-{feature_id}"
        shutil.copytree(extracted_dir, scratch_dir / context_relative)
        context_features.append((feature_id, context_relative, options, container_env))

    dockerfile_content = generate_dockerfile(base_image, context_features)
    (scratch_dir / "Dockerfile").write_text(dockerfile_content)

    client.images.build(
        path=str(scratch_dir), tag=tag, rm=True, nocache=nocache, pull=pull
    )
    return tag
