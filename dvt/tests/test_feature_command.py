from __future__ import annotations

import json

import jsonschema
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app as real_app
from devtemplate.cli_support import describe_app
from devtemplate.commands.feature import app, console

runner = CliRunner()


def _assert_matches_declared_output_schema(command_name: str, payload: dict) -> None:
    schema = describe_app(real_app, version=__version__)["commands"][command_name][
        "output"
    ]["success"]
    jsonschema.validate(instance=payload, schema=schema)


def test_list_reports_no_features_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached features" in result.stdout


def test_list_json_prints_ok_false_when_settings_fail_to_load(monkeypatch):
    from logerr import Err

    from devtemplate.commands import feature as feature_module

    monkeypatch.setattr(
        feature_module, "load_settings", lambda: Err(RuntimeError("bad config"))
    )

    result = runner.invoke(app, ["list", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "bad config" in printed["error"]


def test_list_shows_cached_feature_name_and_description(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "description": "FastAPI web APIs.",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
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
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
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
    _assert_matches_declared_output_schema("feature list", rows)


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


def test_show_json_prints_the_raw_cached_feature_on_success(settings):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )

    result = runner.invoke(app, ["show", "fastapi", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"name": "fastapi"}
    _assert_matches_declared_output_schema("feature show", printed)


def test_show_json_prints_ok_false_on_unknown_feature(settings):
    result = runner.invoke(app, ["show", "nonexistent", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "nonexistent" in printed["error"]


def test_show_error_message_is_not_mangled_by_rich_markup(settings, monkeypatch):
    # Rich's color_system is fixed at Console() construction time (module import),
    # from whatever FORCE_COLOR/TTY state was live then - so in an environment that
    # sets FORCE_COLOR, styled segments get ANSI codes even when writing to
    # CliRunner's non-tty buffer. Force no_color directly so this test checks the
    # actual rendered text, not ANSI-interleaved bytes.
    monkeypatch.setattr(console, "no_color", True)

    result = runner.invoke(app, ["show", "[red]hacked[/red]"])
    assert result.exit_code == 1
    assert "[red]hacked[/red]" in result.stdout


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


def test_sync_shows_a_status_spinner_while_syncing(settings, monkeypatch):
    from logerr import Ok

    sync_calls = []

    def fake_sync(settings_arg, client):
        sync_calls.append(True)
        return Ok(["fastapi"])

    monkeypatch.setattr("devtemplate.commands.feature.sync_templates", fake_sync)

    entered = []

    class FakeStatus:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *exc_info):
            return False

    captured = {}

    def fake_status(message, **kwargs):
        captured["message"] = message
        return FakeStatus()

    monkeypatch.setattr(console, "status", fake_status)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0
    assert entered == [True]
    assert sync_calls == [True]
    assert "sync" in captured["message"].lower()


def test_sync_clears_the_pulled_feature_artifact_cache(settings, monkeypatch):
    # pull_feature (used by `dvt up`) caches an OCI Feature artifact forever
    # once pulled, keyed by the ref string - correct for an immutable
    # version tag, but means a moved `:latest` upstream is otherwise never
    # noticed on a machine that already pulled it (see
    # devtemplate.features.clear_pulled_features's docstring). `sync` is the
    # existing "go get whatever's current" entry point, so a stale pulled
    # artifact from a previous run must be gone by the time it returns.
    from logerr import Ok

    stale = settings.features_dir / "deadbeef"
    stale.mkdir(parents=True)
    (stale / "install.sh").write_text("echo stale\n")

    monkeypatch.setattr(
        "devtemplate.commands.feature.sync_templates",
        lambda settings_arg, client: Ok(["fastapi"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert not stale.exists()


def test_sync_json_prints_ok_true_with_synced_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.feature.sync_templates",
        lambda settings_arg, client: Ok(["fastapi", "agent"]),
    )

    result = runner.invoke(app, ["sync", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "synced": ["fastapi", "agent"]}
    _assert_matches_declared_output_schema("feature sync", printed)


def test_sync_json_prints_ok_false_on_failure(settings, monkeypatch):
    from logerr import Err

    monkeypatch.setattr(
        "devtemplate.commands.feature.sync_templates",
        lambda settings_arg, client: Err(RuntimeError("network unreachable")),
    )

    result = runner.invoke(app, ["sync", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "network unreachable" in printed["error"]


def test_add_merges_into_existing_devcontainer_json(tmp_path, settings, monkeypatch):
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

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output

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


def test_add_json_prints_ok_true_with_added_names(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "cli"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "cli",
                "features": {"ghcr.io/jesserobertson/devcontainers/cli:latest": {}},
            }
        )
    )

    result = runner.invoke(app, ["add", "cli", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed == {"ok": True, "added": ["cli"]}
    _assert_matches_declared_output_schema("feature add", printed)


def test_add_json_prints_ok_false_when_devcontainer_json_missing(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["add", "agent", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "dvt init" in printed["error"]


def test_add_strips_description_field_from_template_before_merging(
    tmp_path, settings, monkeypatch
):
    # Regression test: templates/*/devcontainer.json files carry a
    # "description" field used by 'dvt feature list'/'show' for
    # registry metadata. It is not part of the devcontainer.json spec, so it
    # must never be merged into a consuming project's devcontainer.json - if
    # it leaked through, the merge result would fail schema validation
    # ("Unevaluated properties are not allowed ('description' was
    # unexpected)") since the schema is closed to unknown top-level keys.
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "cli"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "cli",
                "description": "Typer, Rich and Pydantic for building Python CLIs.",
                "features": {"ghcr.io/jesserobertson/devcontainers/cli:latest": {}},
            }
        )
    )

    result = runner.invoke(app, ["add", "cli"])
    assert result.exit_code == 0, result.output

    merged = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert "description" not in merged


def test_add_records_applied_feature_in_sidecar(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
            }
        )
    )

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == [
        {
            "name": "agent",
            "overlay": {
                "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}}
            },
        }
    ]


def test_add_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 1


def test_add_refuses_on_invalid_json(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = '{\n  // a comment\n  "name": "my-project"\n}'
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["add", "agent"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_refuses_when_merge_result_is_schema_invalid(
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

    result = runner.invoke(app, ["add", "broken"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_refuses_when_feature_already_applied(tmp_path, settings, monkeypatch):
    # Regression test: 'add' used to just append to sidecar["applied"] every
    # time, with no idempotency check. Adding the same feature twice doubled
    # fields like runArgs, and 'remove' only pops the *last* matching applied
    # entry (by design, so a legitimately re-added feature can be removed
    # once per add) - so one 'remove' after a double-'add' would report
    # success while the feature was still fully applied. Refusing the second
    # 'add' outright is the smaller, correct fix.
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps({"name": "agent", "runArgs": ["--cap-add=NET_ADMIN"]})
    )

    first = runner.invoke(app, ["add", "agent"])
    assert first.exit_code == 0, first.output

    after_first_add = (devcontainer_dir / "devcontainer.json").read_text()
    sidecar_after_first_add = (devcontainer_dir / "dvt-features.json").read_text()

    second = runner.invoke(app, ["add", "agent"])
    assert second.exit_code == 1, second.output

    assert (devcontainer_dir / "devcontainer.json").read_text() == after_first_add
    assert (
        devcontainer_dir / "dvt-features.json"
    ).read_text() == sidecar_after_first_add


def test_add_refuses_on_corrupt_sidecar_without_writing_devcontainer_json(
    tmp_path, settings, monkeypatch
):
    # Regression test: 'add' used to write devcontainer.json BEFORE loading
    # the sidecar, so a corrupt/unparseable sidecar exited 1 only after the
    # merge result had already been written - violating the documented
    # "byte-for-byte unchanged on any refusal" guarantee. The sidecar must be
    # loaded (and validated) before any write happens.
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = json.dumps(
        {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    )
    (devcontainer_dir / "devcontainer.json").write_text(original)
    (devcontainer_dir / "dvt-features.json").write_text("{ not valid json")

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))

    result = runner.invoke(app, ["add", "agent"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_add_auto_syncs_when_cache_empty(tmp_path, settings, monkeypatch):
    from logerr import Ok

    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "agent"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": "agent",
                    "features": {
                        "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                    },
                }
            )
        )
        return Ok(["agent"])

    monkeypatch.setattr("devtemplate.commands.feature.sync_templates", fake_sync)

    result = runner.invoke(app, ["add", "agent"])
    assert result.exit_code == 0, result.output


def test_add_shows_a_status_spinner_while_auto_syncing(tmp_path, settings, monkeypatch):
    from logerr import Ok

    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    def fake_sync(settings_arg, client):
        template_dir = settings_arg.templates_dir / "agent"
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": "agent",
                    "features": {
                        "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
                    },
                }
            )
        )
        return Ok(["agent"])

    monkeypatch.setattr("devtemplate.commands.feature.sync_templates", fake_sync)

    entered = []

    class FakeStatus:
        def __enter__(self):
            entered.append(True)
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(console, "status", lambda *a, **k: FakeStatus())

    result = runner.invoke(app, ["add", "agent"])

    assert result.exit_code == 0, result.output
    assert entered == [True]


def test_remove_reverts_solo_feature_to_pre_add_state(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
                "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
                "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
                "waitFor": "postStartCommand",
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert "features" not in final
    assert "runArgs" not in final
    assert "postStartCommand" not in final
    assert "waitFor" not in final
    assert final["name"] == "my-project"
    assert final["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []


def test_remove_json_prints_ok_true_with_removed_names(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "cli"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "cli",
                "features": {"ghcr.io/jesserobertson/devcontainers/cli:latest": {}},
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    add_result = runner.invoke(app, ["add", "cli"])
    assert add_result.exit_code == 0, add_result.output

    result = runner.invoke(app, ["remove", "cli", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed == {"ok": True, "removed": ["cli"]}
    _assert_matches_declared_output_schema("feature remove", printed)


def test_remove_json_prints_ok_false_when_not_tracked(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    result = runner.invoke(app, ["remove", "cli", "--json"])

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False


def test_remove_leaves_hand_edited_field_untouched(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "agent"])

    current = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    current["forwardPorts"] = [8000]
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(current))

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["forwardPorts"] == [8000]


def test_remove_earlier_feature_leaves_later_overlapping_field(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name, image in [
        ("fastapi", "ghcr.io/x/base-ubuntu:latest"),
        ("pytorch", "ghcr.io/x/base-cuda:latest"),
    ]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps({"name": template_name, "image": image})
        )

    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "my-project", "image": "ghcr.io/x/base-ubuntu:latest"})
    )
    runner.invoke(app, ["add", "fastapi"])
    runner.invoke(app, ["add", "pytorch"])

    remove_result = runner.invoke(app, ["remove", "fastapi"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["image"] == "ghcr.io/x/base-cuda:latest"


def test_remove_later_feature_restores_earlier_overlapping_field(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name, image in [
        ("fastapi", "ghcr.io/x/base-ubuntu:latest"),
        ("pytorch", "ghcr.io/x/base-cuda:latest"),
    ]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps({"name": template_name, "image": image})
        )

    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "my-project", "image": "ghcr.io/x/base-ubuntu:latest"})
    )
    runner.invoke(app, ["add", "fastapi"])
    runner.invoke(app, ["add", "pytorch"])

    remove_result = runner.invoke(app, ["remove", "pytorch"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["image"] == "ghcr.io/x/base-ubuntu:latest"


def test_remove_restores_pre_existing_hand_set_field_when_no_prior_init(
    tmp_path, settings, monkeypatch
):
    # Regression test: no 'dvt init' ever ran here, so there is no sidecar file
    # yet when 'add' runs. The base devcontainer.json is hand-written and
    # already sets remoteEnv.MY_VAR before dvt ever touches the file. The
    # feature's overlay ALSO sets remoteEnv (overwriting MY_VAR and adding
    # OTHER_VAR). If 'add' fails to capture this pre-existing state as the
    # sidecar's "init" baseline, 'remove' has nothing to restore MY_VAR from
    # and silently deletes it instead of restoring the hand-set value.
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "remoteEnv": {"MY_VAR": "feature-value", "OTHER_VAR": "x"},
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "my-project",
                "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "remoteEnv": {"MY_VAR": "keep-me"},
            }
        )
    )
    assert not (devcontainer_dir / "dvt-features.json").exists()

    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    after_add = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert after_add["remoteEnv"] == {
        "MY_VAR": "feature-value",
        "OTHER_VAR": "x",
    }

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["remoteEnv"] == {"MY_VAR": "keep-me"}


def test_remove_restores_hand_edit_made_between_init_and_first_add(
    tmp_path, settings, monkeypatch
):
    # Regression test: 'dvt init' always writes a sidecar with a real "init"
    # baseline and an empty "applied" list, so a guard that only re-captured
    # "init" when no sidecar file existed yet would never fire for a project
    # scaffolded via 'dvt init' - "init" would stay frozen at whatever
    # 'dvt init' originally wrote. A hand-edit made between 'dvt init' and the
    # first 'dvt feature add' would then be silently wiped out the first time
    # 'remove' ran on a feature touching that field. The correct baseline is
    # "whatever the file looked like right before the first feature was ever
    # layered on" - captured at the first 'add', not at 'dvt init' time -
    # so the fix keys off sidecar["applied"] being empty, not file existence.
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    init_config = {
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
    }
    # Simulate 'dvt init': devcontainer.json plus a sidecar with a real init
    # baseline and an empty applied list.
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(init_config))
    (devcontainer_dir / "dvt-features.json").write_text(
        json.dumps({"init": init_config, "applied": []})
    )

    # Hand-edit made after 'dvt init' but before the first 'dvt feature add'.
    hand_edited = dict(init_config)
    hand_edited["remoteEnv"] = {"MY_VAR": "hand-set-value"}
    (devcontainer_dir / "devcontainer.json").write_text(json.dumps(hand_edited))

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "agent",
                "remoteEnv": {"MY_VAR": "feature-value", "OTHER_VAR": "x"},
            }
        )
    )

    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 0, remove_result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert final["remoteEnv"] == {"MY_VAR": "hand-set-value"}


def test_remove_refuses_and_leaves_no_partial_write_on_schema_invalid_result(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "agent", "image": "ghcr.io/jesserobertson/agent-image:latest"}
        )
    )
    # No 'image', 'dockerFile', or 'dockerComposeFile' here - the feature is
    # the only source of a container type, so removing it should leave the
    # config unable to satisfy the schema's oneOf container requirement.
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps({"name": "my-project"})
    )

    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    pre_remove_config = (devcontainer_dir / "devcontainer.json").read_text()
    pre_remove_sidecar = (devcontainer_dir / "dvt-features.json").read_text()

    remove_result = runner.invoke(app, ["remove", "agent"])
    assert remove_result.exit_code == 1, remove_result.output

    assert (devcontainer_dir / "devcontainer.json").read_text() == pre_remove_config
    assert (devcontainer_dir / "dvt-features.json").read_text() == pre_remove_sidecar


def test_remove_refuses_untracked_feature_name(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    original = json.dumps(
        {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    )
    (devcontainer_dir / "devcontainer.json").write_text(original)

    result = runner.invoke(app, ["remove", "never-added"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == original


def test_remove_refuses_untracked_feature_name_when_sidecar_exists_for_other_feature(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "fastapi"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "fastapi",
                "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    add_result = runner.invoke(app, ["add", "fastapi"])
    assert add_result.exit_code == 0, add_result.output

    pre_remove_config = (devcontainer_dir / "devcontainer.json").read_text()
    pre_remove_sidecar = (devcontainer_dir / "dvt-features.json").read_text()

    result = runner.invoke(app, ["remove", "never-added"])

    assert result.exit_code == 1
    assert (devcontainer_dir / "devcontainer.json").read_text() == pre_remove_config
    assert (devcontainer_dir / "dvt-features.json").read_text() == pre_remove_sidecar


def test_remove_still_works_after_the_template_leaves_the_cache(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))

    add_result = runner.invoke(app, ["add", "agent"])
    assert add_result.exit_code == 0, add_result.output

    # Simulate the template being pruned from the cache (e.g. removed
    # upstream, then 'dvt feature sync') - the sidecar still says it's
    # applied, and remove must still be able to act on that.
    import shutil

    shutil.rmtree(template_dir)

    remove_result = runner.invoke(app, ["remove", "agent"])

    assert remove_result.exit_code == 0, remove_result.output
    assert "Removed feature 'agent'" in remove_result.output


def test_remove_refuses_when_devcontainer_json_missing(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["remove", "agent"])
    assert result.exit_code == 1


def test_add_multiple_names_applies_all_in_order(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )

    result = runner.invoke(app, ["add", "py-devtools", "marimo"])

    assert result.exit_code == 0, result.output
    assert "Added feature 'py-devtools'" in result.output
    assert "Added feature 'marimo'" in result.output

    merged = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {},
        "ghcr.io/jesserobertson/devcontainers/marimo:latest": {},
    }

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["py-devtools", "marimo"]


def test_add_stops_on_first_failure_leaving_earlier_successes_applied(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )

    template_dir = settings.templates_dir / "py-devtools"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "py-devtools",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
                },
            }
        )
    )

    result = runner.invoke(app, ["add", "py-devtools", "typo-name", "marimo"])

    assert result.exit_code == 1
    assert "Added feature 'py-devtools'" in result.output
    # Resolution now fails one step earlier than it used to - at
    # resolve_or_confirm (no cache entry close enough to fuzzy-match
    # "typo-name"), not at add_one's/load_cached_template's uncached-name
    # check - so the message text is resolve_or_confirm's, not
    # load_cached_template's. Control flow (py-devtools already applied
    # before the failure) is unchanged.
    assert "No feature named" in result.output
    assert "typo-name" in result.output
    assert "Added feature 'marimo'" not in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["py-devtools"]


def test_remove_multiple_names_removes_all_in_order(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools", "marimo"])

    result = runner.invoke(app, ["remove", "py-devtools", "marimo"])

    assert result.exit_code == 0, result.output
    assert "Removed feature 'py-devtools'" in result.output
    assert "Removed feature 'marimo'" in result.output

    final = json.loads((devcontainer_dir / "devcontainer.json").read_text())
    assert "features" not in final

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []


def test_remove_stops_on_first_failure_leaving_earlier_removals_applied(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    for template_name in ["py-devtools", "marimo"]:
        template_dir = settings.templates_dir / template_name
        template_dir.mkdir(parents=True)
        (template_dir / "devcontainer.json").write_text(
            json.dumps(
                {
                    "name": template_name,
                    "features": {
                        f"ghcr.io/jesserobertson/devcontainers/{template_name}:latest": {}
                    },
                }
            )
        )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools", "marimo"])

    result = runner.invoke(app, ["remove", "py-devtools", "never-added", "marimo"])

    assert result.exit_code == 1
    assert "Removed feature 'py-devtools'" in result.output
    # "never-added" isn't close enough to fuzzy-match either cached template
    # name, so resolve_or_confirm rejects it before remove_one's own
    # not-tracked check ever runs - message text is resolve_or_confirm's,
    # not remove_one's. Control flow (py-devtools already removed before the
    # failure, marimo never reached) is unchanged.
    assert "No feature named" in result.output
    assert "Removed feature 'marimo'" not in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert [entry["name"] for entry in sidecar["applied"]] == ["marimo"]


def test_remove_same_name_twice_in_one_invocation_fails_on_the_second(
    tmp_path, settings, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()

    template_dir = settings.templates_dir / "py-devtools"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(
        json.dumps(
            {
                "name": "py-devtools",
                "features": {
                    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
                },
            }
        )
    )
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    runner.invoke(app, ["add", "py-devtools"])

    result = runner.invoke(app, ["remove", "py-devtools", "py-devtools"])

    assert result.exit_code == 1
    assert "Removed feature 'py-devtools'" in result.output
    assert "is not tracked for this project" in result.output

    sidecar = json.loads((devcontainer_dir / "dvt-features.json").read_text())
    assert sidecar["applied"] == []


def test_show_fuzzy_resolves_a_close_typo(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["show", "fastpi"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.stdout


def test_show_yes_flag_skips_the_prompt(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")),
    )

    result = runner.invoke(app, ["show", "fastpi", "--yes"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.stdout


def test_show_json_mode_with_typo_fails_with_suggestion_no_hang(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt")),
    )

    result = runner.invoke(app, ["show", "fastpi", "--json"])
    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "fastapi" in printed["error"]


def test_add_fuzzy_resolves_a_close_typo(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["add", "agnt"])

    assert result.exit_code == 0, result.output
    assert "Added feature 'agent'" in result.output


def test_remove_fuzzy_resolves_a_close_typo(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))
    runner.invoke(app, ["add", "agent"])

    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = runner.invoke(app, ["remove", "agnt"])

    assert result.exit_code == 0, result.output
    assert "Removed feature 'agent'" in result.output
