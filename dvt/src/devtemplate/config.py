from __future__ import annotations

import re
from pathlib import Path

import platformdirs
from logerr import Err, Ok, Result
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GITHUB_REPO_PATTERN = re.compile(r"^[\w.-]+/[\w.-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DVT_")

    github_repo: str = "jesserobertson/devcontainers"
    github_branch: str = "main"

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
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"


def load_settings() -> Result[Settings, Exception]:
    """Construct Settings, wrapping any validation failure (e.g. a malformed
    DVT_GITHUB_REPO env var) as an Err instead of letting pydantic.ValidationError
    crash the CLI with a raw traceback. The validators themselves are untouched —
    they still raise, per pydantic's own protocol; this only wraps construction."""
    try:
        return Ok(Settings())
    except Exception as exc:
        return Err(exc)
