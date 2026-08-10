from __future__ import annotations

import json

from devtemplate.sidecar import load_sidecar, sidecar_path, write_sidecar


def test_load_sidecar_defaults_when_missing(tmp_path):
    result = load_sidecar(tmp_path)
    assert result.is_ok()
    assert result.unwrap() == {"init": {}, "applied": []}


def test_write_then_load_round_trips(tmp_path):
    sidecar = {
        "init": {"image": "x"},
        "applied": [{"name": "fastapi", "overlay": {"image": "y"}}],
    }
    write_result = write_sidecar(tmp_path, sidecar)
    assert write_result.is_ok()

    loaded = load_sidecar(tmp_path)
    assert loaded.unwrap() == sidecar


def test_load_sidecar_reports_invalid_json(tmp_path):
    sidecar_path(tmp_path).write_text("not json")

    result = load_sidecar(tmp_path)
    assert result.is_err()


def test_write_sidecar_creates_parent_dir(tmp_path):
    target_dir = tmp_path / ".devcontainer"

    result = write_sidecar(target_dir, {"init": {}, "applied": []})

    assert result.is_ok()
    assert (target_dir / "dvt-features.json").exists()


def test_sidecar_path_is_named_dvt_features_json(tmp_path):
    assert sidecar_path(tmp_path) == tmp_path / "dvt-features.json"


def test_write_sidecar_produces_valid_json(tmp_path):
    write_sidecar(tmp_path, {"init": {"image": "x"}, "applied": []})

    data = json.loads(sidecar_path(tmp_path).read_text())
    assert data == {"init": {"image": "x"}, "applied": []}
