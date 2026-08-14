from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logerr.utilities import wrap_result

__all__ = ["sidecar_path", "load_sidecar", "write_sidecar"]

SIDECAR_FILENAME = "dvt-features.json"


def sidecar_path(devcontainer_dir: Path) -> Path:
    return devcontainer_dir / SIDECAR_FILENAME


@wrap_result
def load_sidecar(devcontainer_dir: Path) -> dict[str, Any]:
    """Load the feature-tracking sidecar, defaulting to an empty one (no init
    baseline, no applied features) if it doesn't exist yet - a project whose
    devcontainer.json wasn't scaffolded by 'dvt init' simply starts tracking
    from here.
    """
    path = sidecar_path(devcontainer_dir)
    if not path.exists():
        return {"init": {}, "applied": []}
    data = json.loads(path.read_text())
    return {"init": data.get("init", {}), "applied": data.get("applied", [])}


@wrap_result
def write_sidecar(devcontainer_dir: Path, sidecar: dict[str, Any]) -> None:
    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path(devcontainer_dir).write_text(json.dumps(sidecar, indent=2) + "\n")
