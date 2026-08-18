from __future__ import annotations

import json

import jsonschema
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app as real_app
from devtemplate.cli_support import describe_app
from devtemplate.commands.image import app

runner = CliRunner()


def _assert_matches_declared_output_schema(command_name: str, payload: dict) -> None:
    schema = describe_app(real_app, version=__version__)["commands"][command_name][
        "output"
    ]["success"]
    jsonschema.validate(instance=payload, schema=schema)


def test_list_reports_no_images_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached images" in result.stdout


def test_list_shows_cached_image_name_and_ref(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps(
            {
                "name": "base-cuda",
                "description": "CUDA base.",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        )
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout
    assert "ghcr.io/jesserobertson/base-cuda:latest" in result.stdout


def test_list_json_output_includes_all_fields(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps(
            {
                "name": "base-cuda",
                "description": "CUDA base.",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        )
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "name": "base-cuda",
            "description": "CUDA base.",
            "ref": "ghcr.io/jesserobertson/base-cuda:latest",
        }
    ]
    _assert_matches_declared_output_schema("image list", rows)


def test_show_prints_cached_image(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )

    result = runner.invoke(app, ["show", "base-cuda"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout


def test_show_json_prints_the_raw_cached_image_on_success(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )

    result = runner.invoke(app, ["show", "base-cuda", "--json"])
    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"}
    _assert_matches_declared_output_schema("image show", printed)


def test_show_fuzzy_resolves_a_close_typo(settings, monkeypatch):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["show", "bas-cuda"])
    assert result.exit_code == 0, result.output
    assert "base-cuda" in result.stdout


def test_sync_reports_synced_image_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Ok(["base-cuda", "base-ubuntu"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout
    assert "base-ubuntu" in result.stdout


def test_sync_json_prints_ok_true_with_synced_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Ok(["base-cuda"]),
    )

    result = runner.invoke(app, ["sync", "--json"])
    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "synced": ["base-cuda"]}
    _assert_matches_declared_output_schema("image sync", printed)


def test_sync_json_prints_ok_false_on_failure(settings, monkeypatch):
    from logerr import Err

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Err(RuntimeError("network unreachable")),
    )

    result = runner.invoke(app, ["sync", "--json"])
    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "network unreachable" in printed["error"]
