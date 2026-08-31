from __future__ import annotations

import json

import jsonschema
import pytest
import typer
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app as real_app
from devtemplate.commands.init import DEFAULT_IMAGE, init
from devtemplate.describe import describe_app

app = typer.Typer()
app.command("init")(init)


@pytest.fixture(autouse=True)
def _no_network_image_sync(monkeypatch):
    """Every test in this file now indirectly triggers init's best-effort
    image-cache auto-sync as a side effect (Fix 5) - default it to a fast,
    no-op Ok([]) so pre-existing tests that don't care about image sync stay
    hermetic, matching this codebase's established convention of never
    hitting real network in unit tests. Tests that DO care about sync
    behavior override this with their own monkeypatch.setattr call.
    """
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.init.sync_images",
        lambda settings_arg, client: Ok([]),
    )


def _assert_matches_declared_output_schema(command_name: str, payload: dict) -> None:
    schema = describe_app(real_app, version=__version__)["commands"][command_name][
        "output"
    ]["success"]
    jsonschema.validate(instance=payload, schema=schema)


@app.command("noop")
def _noop() -> None:
    """No-op command to prevent Typer single-command collapse in tests."""
    pass


runner = CliRunner()


def test_init_scaffolds_devcontainer_json_with_defaults(tmp_path, settings):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

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
    result = runner.invoke(app, ["init", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert DEFAULT_IMAGE in result.output


def test_init_image_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    from logerr import Ok

    def fake_sync(settings_arg, client):
        images_dir = settings_arg.images_dir
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "base-cuda.json").write_text(
            json.dumps(
                {
                    "name": "base-cuda",
                    "description": "",
                    "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                    "aliases": ["cuda"],
                }
            )
        )
        return Ok(["base-cuda"])

    monkeypatch.setattr("devtemplate.commands.init.sync_images", fake_sync)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "cuda"])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_sync_failure_is_non_fatal_and_falls_through(
    tmp_path, settings, monkeypatch
):
    from logerr import Err

    def fake_sync(settings_arg, client):
        return Err(RuntimeError("network unreachable"))

    monkeypatch.setattr("devtemplate.commands.init.sync_images", fake_sync)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--image",
            "ghcr.io/jesserobertson/base-cuda:latest",
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_skips_auto_sync_when_image_is_already_a_literal_ref(
    tmp_path, settings, monkeypatch
):
    # A literal ref (contains "/" or ":") never needs alias resolution, so
    # init must never pay for a network round-trip to resolve it - including
    # the default (unset --image), which is already a literal ref.
    def fail_if_called(settings_arg, client):
        raise AssertionError("sync_images should not be called for a literal ref")

    monkeypatch.setattr("devtemplate.commands.init.sync_images", fail_if_called)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == DEFAULT_IMAGE


def test_init_image_option_overrides_default(tmp_path, settings):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app,
        [
            "init",
            str(project_dir),
            "--image",
            "ghcr.io/jesserobertson/base-cuda:latest",
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_json_prints_ok_true_with_path_on_success(tmp_path, settings):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    target = project_dir / ".devcontainer" / "devcontainer.json"
    assert printed == {"ok": True, "name": "my-project", "path": str(target)}
    _assert_matches_declared_output_schema("init", printed)


def test_init_json_prints_ok_false_when_devcontainer_json_already_exists(tmp_path):
    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text(
        '{"name": "existing"}'
    )

    result = runner.invoke(app, ["init", str(project_dir), "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "already exists" in printed["error"]


def test_init_refuses_to_overwrite_existing_devcontainer_json(tmp_path):
    project_dir = tmp_path / "my-project"
    (project_dir / ".devcontainer").mkdir(parents=True)
    (project_dir / ".devcontainer" / "devcontainer.json").write_text(
        '{"name": "existing"}'
    )

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 1
    assert (
        json.loads((project_dir / ".devcontainer" / "devcontainer.json").read_text())[
            "name"
        ]
        == "existing"
    )


def test_init_derives_name_from_target_directory(tmp_path, settings):
    project_dir = tmp_path / "my-actual-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["name"] == "my-actual-project"


def test_init_scaffolds_pixi_toml_when_absent(tmp_path, settings):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    pixi_toml = project_dir / "pixi.toml"
    assert pixi_toml.exists()
    content = pixi_toml.read_text()
    assert 'name = "my-project"' in content
    assert '"conda-forge"' in content
    assert '"linux-64"' in content


def test_init_does_not_overwrite_existing_pixi_toml(tmp_path, settings):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pixi.toml").write_text('[project]\nname = "already-here"\n')

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (
        project_dir / "pixi.toml"
    ).read_text() == '[project]\nname = "already-here"\n'


def test_init_does_not_write_pixi_toml_when_pyproject_toml_exists(tmp_path, settings):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text('[tool.pixi.project]\nname = "x"\n')

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert not (project_dir / "pixi.toml").exists()


def test_init_writes_sidecar_with_init_block(tmp_path, settings):
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    sidecar = json.loads(
        (project_dir / ".devcontainer" / "dvt-features.json").read_text()
    )
    assert sidecar["applied"] == []
    assert sidecar["init"]["image"] == DEFAULT_IMAGE
    assert "pixi install" in sidecar["init"]["postCreateCommand"]


def _write_image_registry(settings, images):
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    for image in images:
        (settings.images_dir / f"{image['name']}.json").write_text(json.dumps(image))


def test_init_image_resolves_alias_via_cached_registry(tmp_path, settings):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        ],
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "cuda"])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_resolves_close_typo_with_confirm(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "bas-cuda"])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_declining_confirm_writes_nothing(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "bas-cuda"])

    assert result.exit_code == 1
    assert not (project_dir / ".devcontainer" / "devcontainer.json").exists()


def test_init_image_yes_flag_skips_the_prompt(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")),
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, ["init", str(project_dir), "--image", "bas-cuda", "--yes"]
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_json_mode_fails_with_suggestion_no_hang(
    tmp_path, settings, monkeypatch
):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")),
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, ["init", str(project_dir), "--image", "bas-cuda", "--json"]
    )

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "base-cuda" in printed["error"]
