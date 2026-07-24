import json

from typer.testing import CliRunner

from devtemplate.commands.template import app, console

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


def test_show_refuses_cleanly_on_unknown_template(settings):
    result = runner.invoke(app, ["show", "nonexistent"])
    assert result.exit_code == 1
    assert "nonexistent" in result.stdout


def test_show_error_message_is_not_mangled_by_rich_markup(settings, monkeypatch):
    # Rich's color_system is fixed at Console() construction time (module import),
    # from whatever FORCE_COLOR/TTY state was live then — so in an environment that
    # sets FORCE_COLOR (e.g. this dev shell), styled segments get ANSI codes even
    # when writing to CliRunner's non-tty buffer, regardless of later env changes.
    # Force no_color directly so this test checks the actual rendered text, not
    # ANSI-interleaved bytes.
    monkeypatch.setattr(console, "no_color", True)

    result = runner.invoke(app, ["show", ".."])
    assert result.exit_code == 1
    assert "[a-z0-9][a-z0-9-]" in result.stdout


def test_sync_reports_synced_template_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.template.sync_templates",
        lambda settings_arg, client: Ok(["fastapi", "agent"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "fastapi" in result.stdout
    assert "agent" in result.stdout
