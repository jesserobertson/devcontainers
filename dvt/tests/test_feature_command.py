from __future__ import annotations

import json

from typer.testing import CliRunner

from devtemplate.commands.feature import app, console

runner = CliRunner()


def test_list_reports_no_features_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached features" in result.stdout


def test_list_shows_cached_feature_name_and_description(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "description": "FastAPI web APIs.",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "FastAPI web APIs." in result.stdout


def test_list_json_output_includes_all_fields(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "description": "FastAPI web APIs.",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "name": "fastapi",
            "description": "FastAPI web APIs.",
            "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
            "feature_ref": "ghcr.io/jesserobertson/devcontainers/fastapi:latest",
        }
    ]


def test_list_json_output_defaults_missing_description_to_empty_string(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "cli").mkdir()
    (settings.templates_dir / "cli" / "devcontainer.json").write_text(
        json.dumps({"name": "cli", "image": "ghcr.io/x", "features": {}})
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows[0]["description"] == ""


def test_list_json_output_empty_cache_returns_empty_array(settings):
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_list_json_output_skips_broken_entry_without_polluting_stdout(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    (settings.templates_dir / "broken").mkdir()
    (settings.templates_dir / "broken" / "devcontainer.json").write_text(
        "{ invalid json"
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    # Verify stdout is pure JSON and contains only the well-formed entry
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["name"] == "fastapi"


def test_show_prints_cached_feature(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )

    result = runner.invoke(app, ["show", "fastapi"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_show_refuses_cleanly_on_unknown_feature(settings):
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code == 1
    assert "nonexistent" in result.stdout


def test_show_error_message_is_not_mangled_by_rich_markup(settings, monkeypatch):
    # Rich's color_system is fixed at Console() construction time (module import),
    # from whatever FORCE_COLOR/TTY state was live then - so in an environment that
    # sets FORCE_COLOR, styled segments get ANSI codes even when writing to
    # CliRunner's non-tty buffer. Force no_color directly so this test checks the
    # actual rendered text, not ANSI-interleaved bytes.
    monkeypatch.setattr(console, "no_color", True)

    result = runner.invoke(app, ["show", ".."])
    assert result.exit_code == 1
    assert "[a-z0-9][a-z0-9-]" in result.stdout


def test_sync_reports_synced_feature_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.feature.sync_templates",
        lambda settings_arg, client: Ok(["fastapi", "agent"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
