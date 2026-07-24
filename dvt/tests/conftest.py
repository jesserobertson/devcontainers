from __future__ import annotations

import logerr
import pytest

from devtemplate.config import Settings

logerr.configure(enabled=False)


def pytest_collection_modifyitems(items: list) -> None:
    for item in items:
        if not any(
            any(item.iter_markers(name=name))
            for name in ("integration", "slow", "network")
        ):
            item.add_marker("unit")


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setattr(
        "devtemplate.config.platformdirs.user_data_dir", lambda name: str(tmp_path)
    )
    return Settings()
