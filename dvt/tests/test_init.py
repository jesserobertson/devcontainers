from __future__ import annotations

import json

import typer
from typer.testing import CliRunner

from devtemplate.commands.init import DEFAULT_IMAGE, init

app = typer.Typer()
app.command("init")(init)

runner = CliRunner()


def test_init_scaffolds_devcontainer_json_with_defaults(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-project"
    assert written["image"] == DEFAULT_IMAGE
    assert written["remoteUser"] == "dev"
    assert written["workspaceFolder"] == "/workspace"
    assert "features" not in written
    post_create = written["postCreateCommand"]
    assert "detached-environments = true" in post_create
    assert post_create.endswith("pixi install")
    assert post_create.index("detached-environments") < post_create.index(
        "pixi install"
    )


def test_init_help_text_mentions_default_image():
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert DEFAULT_IMAGE in result.output


def test_init_image_option_overrides_default(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, [str(project_dir), "--image", "ghcr.io/jesserobertson/base-cuda:latest"]
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path):
    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text(
        '{"name": "existing"}'
    )

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 1
    assert (
        json.loads(
            (project_dir / ".devcontainer" / "devcontainer.json").read_text()
        )["name"]
        == "existing"
    )


def test_init_derives_name_from_target_directory(tmp_path):
    project_dir = tmp_path / "my-actual-project"

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-actual-project"


def test_init_scaffolds_pixi_toml_when_absent(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    pixi_toml = project_dir / "pixi.toml"
    assert pixi_toml.exists()
    content = pixi_toml.read_text()
    assert 'name = "my-project"' in content
    assert '"conda-forge"' in content
    assert '"linux-64"' in content


def test_init_does_not_overwrite_existing_pixi_toml(tmp_path):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pixi.toml").write_text('[project]\nname = "already-here"\n')

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (
        project_dir / "pixi.toml"
    ).read_text() == '[project]\nname = "already-here"\n'


def test_init_does_not_write_pixi_toml_when_pyproject_toml_exists(tmp_path):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text('[tool.pixi.project]\nname = "x"\n')

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    assert not (project_dir / "pixi.toml").exists()


def test_init_writes_sidecar_with_init_block(tmp_path):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, [str(project_dir)])

    assert result.exit_code == 0, result.output
    sidecar = json.loads(
        (project_dir / ".devcontainer" / "dvt-features.json").read_text()
    )
    assert sidecar["applied"] == []
    assert sidecar["init"]["image"] == DEFAULT_IMAGE
    assert "pixi install" in sidecar["init"]["postCreateCommand"]
