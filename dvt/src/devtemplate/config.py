from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, cast

import platformdirs
from logerr import Result
from logerr.utilities import execute
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "load_settings"]

GITHUB_REPO_PATTERN = re.compile(r"^[\w.-]+/[\w.-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DVT_")

    github_repo: str = "jesserobertson/devcontainers"
    github_branch: str = "main"
    runtime: Literal["auto", "docker", "podman"] = "auto"
    podman_machine_auto_init: bool = False
    podman_machine_auto_start: bool = True

    @field_validator("github_repo")
    @classmethod
    def _validate_github_repo(cls, value: str) -> str:
        if not GITHUB_REPO_PATTERN.fullmatch(value):
            raise ValueError(f"github_repo must be in 'owner/repo' form, got {value!r}")
        return value

    @field_validator("github_branch")
    @classmethod
    def _validate_github_branch(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                f"github_branch must be a non-empty name with no leading/trailing whitespace, got {value!r}"
            )
        return value

    @property
    def data_dir(self) -> Path:
        return Path(platformdirs.user_data_dir("dvt"))

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def features_dir(self) -> Path:
        return self.data_dir / "features"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def image_manifest_path(self) -> Path:
        return self.data_dir / "image_manifest.json"


def load_settings() -> Result[Settings, Exception]:
    """Construct Settings, wrapping any validation failure (e.g. a malformed
    DVT_GITHUB_REPO env var) as an Err instead of letting pydantic.ValidationError
    crash the CLI with a raw traceback. The validators themselves are untouched —
    they still raise, per pydantic's own protocol; this only wraps construction."""
    return cast(Result[Settings, Exception], execute(Settings))
