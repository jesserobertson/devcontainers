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


@pytest.fixture
def deps_env_cyclic_cache(settings):
    """A feature-spec cache holding a genuine dependsOn cycle (a -> b -> a).
    'dvt feature deps' uses unwrap_or_exit, so unlike 'list'/'add' it must
    exit non-zero with a cycle message."""
    settings.features_dir.mkdir(parents=True)
    for this, other in (("a", "b"), ("b", "a")):
        feature_dir = settings.features_dir / this
        feature_dir.mkdir()
        (feature_dir / "devcontainer-feature.json").write_text(
            json.dumps(
                {
                    "id": this,
                    "dependsOn": {f"ghcr.io/jesserobertson/devcontainers/{other}": {}},
                }
            )
        )
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
    assert out.startswith("graph TD")
    assert 'rapids["rapids"] --> pixi["pixi"]' in out


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


def test_deps_leaf_target_reports_no_dependson(deps_env_with_cache, capsys):
    # 'pixi' has no dependsOn - the tree branch must still print something
    # (an empty stdout with exit 0 reads like a crash).
    deps_cmd(name="pixi", fmt="tree", json_output=False)
    assert "pixi (no dependsOn)" in capsys.readouterr().out


def test_deps_unknown_feature_name_errors(deps_env_with_cache, capsys):
    # An unresolvable feature name fails the fuzzy resolve (json mode so it
    # can't hang on a prompt) - non-zero exit, {"ok": false} payload.
    with pytest.raises(typer.Exit) as excinfo:
        deps_cmd(name="totally-unknown-feature", fmt="tree", json_output=True)
    assert excinfo.value.exit_code != 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is False


def test_deps_cyclic_cache_exits_nonzero(deps_env_cyclic_cache, capsys):
    with pytest.raises(typer.Exit) as excinfo:
        deps_cmd(name=None, fmt="tree", json_output=False)
    assert excinfo.value.exit_code != 0
    assert "cycle" in capsys.readouterr().out
