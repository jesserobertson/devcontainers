from __future__ import annotations

import logerr
import pytest

from devtemplate.config import Settings

logerr.configure(enabled=False)


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setattr(
        "devtemplate.config.platformdirs.user_data_dir", lambda name: str(tmp_path)
    )
    return Settings()
