import json

from typer.testing import CliRunner

from devtemplate.commands.project import app

runner = CliRunner()


def test_init_scaffolds_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    target = project_dir / ".devcontainer" / "devcontainer.json"
    assert target.exists()
    assert json.loads(target.read_text())["name"] == "my-project"


def test_init_scaffolds_pixi_toml_when_absent(tmp_path, settings):
    """Templates' postCreateCommand is typically 'pixi install' (see cli/
    fastapi/agent), which fails outright in a freshly-scaffolded directory
    with no pixi.toml to install from. init should leave a minimal one so
    the documented quickstart flow works end to end without a manual step."""
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    pixi_toml = project_dir / "pixi.toml"
    assert pixi_toml.exists()
    content = pixi_toml.read_text()
    assert 'name = "my-project"' in content
    assert '"conda-forge"' in content
    assert '"linux-64"' in content


def test_init_does_not_overwrite_existing_pixi_toml(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pixi.toml").write_text('[project]\nname = "already-here"\n')

    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert (project_dir / "pixi.toml").read_text() == '[project]\nname = "already-here"\n'


def test_init_prepends_pixi_detached_environments_step(tmp_path, settings):
    """A template's postCreateCommand ('pixi install') installs into
    <project>/.pixi/envs by default - i.e. onto the workspaceMount bind
    mount. On at least Podman's WSL2 machine on Windows, that bind mount
    can't have file permissions/timestamps set on it, which is exactly what
    pixi's package-linking step needs to do, so install fails outright.
    Prepending a step that turns on pixi's 'detached-environments' config
    moves the installed env into pixi's own cache dir instead (which these
    templates already mount as a real volume for package caching)."""
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "postCreateCommand": "pixi install",
            }
        )
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    post_create = written["postCreateCommand"]
    assert "detached-environments = true" in post_create
    assert ".config/pixi/config.toml" in post_create
    assert post_create.endswith("pixi install")
    assert post_create.index("detached-environments") < post_create.index(
        "pixi install"
    )


def test_init_leaves_non_pixi_postcreate_command_untouched(tmp_path, settings):
    template_dir = settings.templates_dir / "node"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "node",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "postCreateCommand": "npm install",
            }
        )
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "node"])

    assert result.exit_code == 0
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["postCreateCommand"] == "npm install"


def test_init_prepends_pixi_step_to_list_form_postcreate_command(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "postCreateCommand": ["pixi install", "pixi run setup"],
            }
        )
    )

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    post_create = written["postCreateCommand"]
    assert isinstance(post_create, list)
    assert post_create[-2:] == ["pixi install", "pixi run setup"]
    assert "detached-environments = true" in post_create[0]


def test_init_does_not_write_pixi_toml_when_pyproject_toml_exists(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text("[tool.pixi.project]\nname = \"x\"\n")

    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert not (project_dir / "pixi.toml").exists()


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text(
        '{"name": "existing"}'
    )

    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 1
    assert (
        json.loads((project_dir / ".devcontainer" / "devcontainer.json").read_text())[
            "name"
        ]
        == "existing"
    )


def test_init_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    from logerr import Ok

    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "fastapi"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": "fastapi",
                    "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                }
            )
        )
        return Ok(["fastapi"])

    monkeypatch.setattr("devtemplate.commands.project.sync_templates", fake_sync)

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    assert (project_dir / ".devcontainer" / "devcontainer.json").exists()


def test_init_refuses_when_template_is_schema_invalid(tmp_path, settings):
    template_dir = settings.templates_dir / "broken"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"remoteUser": 12345}))

    project_dir = tmp_path / "my-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "broken"])

    assert result.exit_code == 1
    assert not (project_dir / ".devcontainer" / "devcontainer.json").exists()


def test_init_derives_name_from_target_directory(tmp_path, settings):
    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "fastapi", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    project_dir = tmp_path / "my-actual-project"
    result = runner.invoke(app, ["init", str(project_dir), "--template", "fastapi"])

    assert result.exit_code == 0
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-actual-project"


def test_add_feature_merges_into_existing_devcontainer_json(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "my-project",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
                "remoteUser": "dev",
            }
        )
    )

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "workspaceFolder": "/workspace",
                "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
                "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
                "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
                "waitFor": "postStartCommand",
                "remoteUser": "dev",
            }
        )
    )

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


def test_add_feature_refuses_when_devcontainer_json_missing(
    tmp_path, settings, monkeypatch
):
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


def test_add_feature_refuses_when_merge_result_is_schema_invalid(
    tmp_path, settings, monkeypatch
):
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
