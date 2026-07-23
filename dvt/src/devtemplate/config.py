from __future__ import annotations

from pathlib import Path

import platformdirs
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DVT_")

    github_repo: str = "jesserobertson/devcontainers"
    github_branch: str = "main"

    @property
    def data_dir(self) -> Path:
        return Path(platformdirs.user_data_dir("dvt"))

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"
