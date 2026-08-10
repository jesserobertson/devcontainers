from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logerr import Err, Ok, Result

SIDECAR_FILENAME = "dvt-features.json"


def sidecar_path(devcontainer_dir: Path) -> Path:
    return devcontainer_dir / SIDECAR_FILENAME


def load_sidecar(devcontainer_dir: Path) -> Result[dict[str, Any], Exception]:
    """Load the feature-tracking sidecar, defaulting to an empty one (no init
    baseline, no applied features) if it doesn't exist yet - a project whose
    devcontainer.json wasn't scaffolded by 'dvt init' simply starts tracking
    from here.
    """
    path = sidecar_path(devcontainer_dir)
    if not path.exists():
        return Ok({"init": {}, "applied": []})
    try:
        data = json.loads(path.read_text())
        return Ok({"init": data.get("init", {}), "applied": data.get("applied", [])})
    except Exception as exc:
        return Err(exc)


def write_sidecar(
    devcontainer_dir: Path, sidecar: dict[str, Any]
) -> Result[None, Exception]:
    try:
        devcontainer_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path(devcontainer_dir).write_text(json.dumps(sidecar, indent=2) + "\n")
        return Ok(None)
    except Exception as exc:
        return Err(exc)
