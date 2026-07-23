import pytest
from pydantic import ValidationError

from devtemplate.models import DevContainerConfig


def test_round_trip_preserves_explicitly_set_fields():
    data = {
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "runArgs": ["--gpus", "all"],
        "remoteUser": "dev",
    }
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data


def test_preserves_unknown_fields():
    data = {"customSetting": "value"}
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data


def test_rejects_non_dict_features():
    with pytest.raises(ValidationError):
        DevContainerConfig.model_validate({"features": "not-a-dict"})


def test_rejects_non_list_run_args():
    with pytest.raises(ValidationError):
        DevContainerConfig.model_validate({"runArgs": "not-a-list"})


def test_accepts_named_object_lifecycle_command():
    data = {"postCreateCommand": {"a": "cmd1", "b": ["cmd2", "arg"]}}
    config = DevContainerConfig.model_validate(data)
    assert config.model_dump(exclude_defaults=True) == data
