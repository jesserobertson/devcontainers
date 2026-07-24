from __future__ import annotations

import jsonschema
import pytest

from devtemplate.schema import validate_devcontainer_config


def test_valid_devcontainer_config_passes():
    validate_devcontainer_config(
        {
            "name": "fastapi",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
            "mounts": [
                "source=fastapi-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
            ],
            "postCreateCommand": "pixi install",
            "remoteUser": "dev",
        }
    )


def test_valid_config_with_run_args_and_lifecycle_commands_passes():
    validate_devcontainer_config(
        {
            "name": "agent",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
            "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
            "postCreateCommand": "pixi install",
            "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
            "waitFor": "postStartCommand",
            "remoteUser": "dev",
        }
    )


def test_missing_image_or_dockerfile_or_compose_rejected():
    with pytest.raises(jsonschema.ValidationError):
        validate_devcontainer_config({"name": "no-build-source"})


def test_features_must_be_an_object():
    with pytest.raises(jsonschema.ValidationError):
        validate_devcontainer_config({"image": "ghcr.io/x", "features": "not-a-dict"})
