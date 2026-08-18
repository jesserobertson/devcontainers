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


def test_create_writes_images_json_in_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    result = runner.invoke(
        app,
        [
            "create",
            "base-ubuntu",
            "--ref",
            "ghcr.io/jesserobertson/base-ubuntu:latest",
            "--description",
            "Ubuntu base.",
            "--alias",
            "ubuntu",
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads((tmp_path / "images" / "base-ubuntu.json").read_text())
    assert written == {
        "name": "base-ubuntu",
        "description": "Ubuntu base.",
        "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "aliases": ["ubuntu"],
    }


def test_create_json_prints_ok_true_with_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    result = runner.invoke(
        app,
        ["create", "base-ubuntu", "--ref", "x", "--description", "y", "--json"],
    )

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["ok"] is True
    assert printed["name"] == "base-ubuntu"
    _assert_matches_declared_output_schema("image create", printed)


def test_create_fails_outside_a_git_checkout(tmp_path, monkeypatch):
    lonely = tmp_path / "no-git-here"
    lonely.mkdir()
    monkeypatch.chdir(lonely)

    result = runner.invoke(
        app, ["create", "base-ubuntu", "--ref", "x", "--description", "y"]
    )

    assert result.exit_code == 1


def test_update_edits_the_existing_repo_local_file(tmp_path, monkeypatch, settings):
    # repo_dir is a SUBdirectory of tmp_path, not tmp_path itself: the settings
    # fixture points settings.images_dir at tmp_path/"images" (data_dir == tmp_path),
    # so a repo root directly at tmp_path would make repo_dir/"images" collide with
    # settings.images_dir on disk - update's fuzzy-resolved <name> argument needs
    # both directories to exist independently (the XDG cache for name resolution,
    # the repo checkout for the actual file being edited).
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text(
        json.dumps(
            {"name": "base-ubuntu", "description": "old", "ref": "x", "aliases": []}
        )
    )
    # update's `<name>` argument is fuzzy-resolved against the cached image
    # registry (Task 2's decorator), so the local XDG cache needs the name too.
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text(
        json.dumps({"name": "base-ubuntu"})
    )

    result = runner.invoke(app, ["update", "base-ubuntu", "--description", "new"])

    assert result.exit_code == 0, result.output
    written = json.loads((repo_dir / "images" / "base-ubuntu.json").read_text())
    assert written["description"] == "new"


def test_update_works_on_an_image_just_created_with_no_prior_sync(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    create_result = runner.invoke(
        app,
        [
            "create",
            "my-image",
            "--ref",
            "ghcr.io/x/my-image:latest",
            "--description",
            "d",
        ],
    )
    assert create_result.exit_code == 0, create_result.output

    update_result = runner.invoke(
        app, ["update", "my-image", "--description", "new description"]
    )

    assert update_result.exit_code == 0, update_result.output
    written = json.loads((tmp_path / "images" / "my-image.json").read_text())
    assert written["description"] == "new description"


def test_delete_removes_the_repo_local_file(tmp_path, monkeypatch, settings):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = runner.invoke(app, ["delete", "base-ubuntu"])

    assert result.exit_code == 0, result.output
    assert not (repo_dir / "images" / "base-ubuntu.json").exists()


def test_delete_json_prints_ok_true_with_path(tmp_path, monkeypatch, settings):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = runner.invoke(app, ["delete", "base-ubuntu", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["ok"] is True
    _assert_matches_declared_output_schema("image delete", printed)
