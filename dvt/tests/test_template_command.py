import json

from typer.testing import CliRunner

from devtemplate.commands.template import app

runner = CliRunner()


def test_list_reports_no_templates_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached templates" in result.stdout


def test_list_shows_cached_template_names(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi", "image": "ghcr.io/x", "features": {"a": {}}})
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_show_prints_cached_template(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )

    result = runner.invoke(app, ["show", "fastapi"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout


def test_sync_reports_synced_template_names(settings, monkeypatch):
    monkeypatch.setattr(
        "devtemplate.commands.template.sync_templates",
        lambda settings_arg, client: ["fastapi", "agent"],
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
