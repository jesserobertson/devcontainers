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
    from logerr import Ok

    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "fastapi"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(json.dumps({"name": "fastapi"}))
        return Ok(["fastapi"])

    monkeypatch.setattr("devtemplate.commands.project.sync_templates", fake_sync)

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert (project_dir / ".devcontainer" / "devcontainer.json").exists()


def test_add_feature_merges_into_existing_devcontainer_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps({
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "remoteUser": "dev",
    }))

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({
        "name": "agent",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "workspaceFolder": "/workspace",
        "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
        "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
        "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
        "waitFor": "postStartCommand",
        "remoteUser": "dev",
    }))

    result = runner.invoke(app, ["add-feature", "agent"])
    assert result.exit_code == 0

    merged = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert merged["name"] == "my-project"
    assert "workspaceFolder" not in merged
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
        "ghcr.io/jesserobertson/devcontainers/agent:latest": {},
    }
    assert merged["runArgs"] == ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]
    assert merged["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert merged["waitFor"] == "postStartCommand"


def test_add_feature_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add-feature", "agent"])
    assert result.exit_code == 1


def test_add_feature_refuses_on_invalid_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = '{\n  // a comment\n  "name": "my-project"\n}'
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["add-feature", "agent"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_feature_refuses_when_merge_result_is_schema_invalid(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = json.dumps(
        {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    )
    (devcontainer_dir / "devcontainer.json").write_text(original)

    template_dir = settings.templates_dir / "broken"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"remoteUser": 12345}))

    result = runner.invoke(app, ["add-feature", "broken"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original
