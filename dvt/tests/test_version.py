from __future__ import annotations

import tomllib
from pathlib import Path

from devtemplate import __version__

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"


def test_version_matches_pyproject_toml():
    data = tomllib.loads(PYPROJECT_PATH.read_text())
    assert __version__ == data["project"]["version"]
