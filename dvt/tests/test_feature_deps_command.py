from __future__ import annotations

import json

import pytest
import typer

from devtemplate.commands.feature import deps as deps_cmd


@pytest.fixture
def deps_env_with_cache(settings):
    """A populated feature-spec cache under settings.features_dir where
    rapids dependsOn pixi (and pixi has no ordering metadata) - enough for
    'dvt feature deps' to render a tree / dot / mermaid / JSON view."""
    settings.features_dir.mkdir(parents=True)
    rapids_feature = settings.features_dir / "rapids"
    rapids_feature.mkdir()
    (rapids_feature / "devcontainer-feature.json").write_text(
        json.dumps(
            {
                "id": "rapids",
                "dependsOn": {"ghcr.io/jesserobertson/devcontainers/pixi": {}},
            }
        )
    )
    pixi_feature = settings.features_dir / "pixi"
    pixi_feature.mkdir()
    (pixi_feature / "devcontainer-feature.json").write_text(json.dumps({"id": "pixi"}))
    return settings


@pytest.fixture
def deps_env_no_cache(settings):
    """No feature-spec cache at all - 'dvt feature deps' must exit 0 with an
    empty JSON object / a stderr hint rather than crashing."""
    return settings


def test_deps_single_tree(deps_env_with_cache, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=False)
    out = capsys.readouterr().out
    assert "rapids" in out and "pixi" in out


def test_deps_single_json(deps_env_with_cache, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=True)
    assert json.loads(capsys.readouterr().out) == {
        "rapids": {"pulls_in": ["pixi"], "installs_after": []}
    }


def test_deps_fleet_dot(deps_env_with_cache, capsys):
    deps_cmd(name=None, fmt="dot", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("digraph") and '"rapids" -> "pixi";' in out


def test_deps_fleet_mermaid(deps_env_with_cache, capsys):
    deps_cmd(name=None, fmt="mermaid", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("graph TD") and "rapids --> pixi" in out


def test_deps_empty_cache_json(deps_env_no_cache, capsys):
    with pytest.raises(typer.Exit) as excinfo:
        deps_cmd(name=None, fmt="tree", json_output=True)
    assert excinfo.value.exit_code == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_deps_empty_cache_stderr(deps_env_no_cache, capsys):
    with pytest.raises(typer.Exit) as excinfo:
        deps_cmd(name=None, fmt="tree", json_output=False)
    assert excinfo.value.exit_code == 0
    assert "Run 'dvt sync' first" in capsys.readouterr().err
