import json

from typer.testing import CliRunner

from devtemplate.commands.project import app

runner = CliRunner()



def test_init_scaffolds_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"})
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    target = project_dir / ".devcontainer" / "devcontainer.json"
    assert target.exists()
    assert json.loads(target.read_text())["name"] == "fastapi"


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "fastapi"}))

    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text('{"name": "existing"}')

    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 1
    assert json.loads((project_dir / ".devcontainer" / "devcontainer.json").read_text())["name"] == "existing"


def test_init_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "fastapi"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(json.dumps({"name": "fastapi"}))
        return ["fastapi"]

    monkeypatch.setattr("devtemplate.commands.project.sync_templates", fake_sync)

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert (project_dir / ".devcontainer" / "devcontainer.json").exists()
