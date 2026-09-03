from __future__ import annotations

import json

import jsonschema
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app

runner = CliRunner()


def _describe() -> dict:
    result = runner.invoke(app, ["--describe"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _describe_scoped(*args: str) -> dict:
    result = runner.invoke(app, [*args, "--describe"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_describe_exits_zero_and_prints_valid_json():
    result = runner.invoke(app, ["--describe"])
    assert result.exit_code == 0
    json.loads(result.output)


def test_describe_reports_dvt_version():
    assert _describe()["dvt_version"] == __version__


def test_describe_lists_every_top_level_command():
    commands = _describe()["commands"]
    for name in ("init", "info", "up", "ssh", "stop", "delete", "sync"):
        assert name in commands, f"{name!r} missing from --describe output"


def test_describe_lists_feature_subcommands_with_a_dotted_name():
    commands = _describe()["commands"]
    for name in (
        "feature list",
        "feature show",
        "feature add",
        "feature remove",
    ):
        assert name in commands, f"{name!r} missing from --describe output"


def test_describe_up_command_lists_its_args():
    up = _describe()["commands"]["up"]
    arg_names = {arg["name"] for arg in up["args"]}
    assert arg_names == {"name", "rebuild", "json_output"}


def test_describe_marks_argument_vs_option_kind():
    up_args = {arg["name"]: arg for arg in _describe()["commands"]["up"]["args"]}
    assert up_args["name"]["kind"] == "argument"
    assert up_args["rebuild"]["kind"] == "option"


def test_describe_includes_flags_for_options():
    up_args = {arg["name"]: arg for arg in _describe()["commands"]["up"]["args"]}
    assert up_args["rebuild"]["flags"] == ["--rebuild"]


def test_describe_includes_command_description():
    up = _describe()["commands"]["up"]
    assert "Build and run a workspace" in up["description"]


def test_describe_excludes_hidden_params():
    # ssh --stdio is deliberately hidden from --help (see cli.py) - it
    # should stay hidden from --describe for the same reason.
    ssh_args = {arg["name"] for arg in _describe()["commands"]["ssh"]["args"]}
    assert "stdio" not in ssh_args
    assert "name" in ssh_args


def test_describe_documents_the_output_shape_for_a_json_capable_command():
    # Real JSON Schema (via Pydantic's model_json_schema()), not a
    # hand-rolled mini-format - see devtemplate.cli_output_schemas.
    up = _describe()["commands"]["up"]
    success = up["output"]["success"]
    assert success["type"] == "object"
    assert set(success["properties"].keys()) == {"ok", "name"}
    assert set(success["required"]) == {"ok", "name"}

    error = up["output"]["error"]
    assert set(error["properties"].keys()) == {"ok", "error"}


def test_describe_output_schema_is_valid_json_schema():
    # Every command's declared output schema must itself be well-formed
    # JSON Schema - not just "some dict that looks schema-shaped" - since
    # the whole point is that a consumer can feed it straight into a real
    # JSON Schema validator.
    for described in _describe()["commands"].values():
        output = described.get("output")
        if output is None:
            continue
        jsonschema.Draft202012Validator.check_schema(output["success"])
        jsonschema.Draft202012Validator.check_schema(output["error"])


def test_describe_documents_a_nested_output_shape_for_info():
    info = _describe()["commands"]["info"]
    success = info["output"]["success"]
    assert set(success["properties"].keys()) == {
        "ok",
        "project",
        "runtime_reachable",
        "workspace",
    }
    project_ref = success["properties"]["project"]["$ref"]
    project_def_name = project_ref.rsplit("/", 1)[-1]
    project_schema = success["$defs"][project_def_name]
    assert set(project_schema["properties"].keys()) == {
        "name",
        "path",
        "image",
        "features",
        "features_tracked",
    }


def test_describe_omits_output_for_a_command_with_no_json_mode():
    # ssh has no --json flag at all (see commands.md) - describe shouldn't
    # claim an output contract that doesn't exist.
    ssh = _describe()["commands"]["ssh"]
    assert "output" not in ssh


def test_describe_documents_bare_array_output_for_feature_list():
    feature_list = _describe()["commands"]["feature list"]
    assert feature_list["output"]["success"]["type"] == "array"


def test_describe_after_a_leaf_command_scopes_to_just_that_command():
    # `dvt up --describe` mirrors `dvt up --help`: the flag rides the
    # subcommand and the manifest covers only that command, so an agent
    # discovering one command doesn't pay for the whole tree.
    scoped = _describe_scoped("up")
    assert set(scoped["commands"]) == {"up"}
    assert "Build and run a workspace" in scoped["commands"]["up"]["description"]


def test_describe_after_a_group_scopes_to_that_group_subtree():
    scoped = _describe_scoped("feature")
    assert set(scoped["commands"]) == {
        "feature list",
        "feature show",
        "feature deps",
        # "feature tree" is a hidden alias of "feature deps"; describe_app
        # walks the Click command tree without a hidden filter, so it is
        # still emitted here (the same command surface under a second name).
        "feature tree",
        "feature add",
        "feature remove",
    }


def test_describe_after_a_nested_leaf_scopes_to_just_that_command():
    scoped = _describe_scoped("feature", "add")
    assert set(scoped["commands"]) == {"feature add"}


def test_describe_scoped_still_reports_dvt_version():
    assert _describe_scoped("up")["dvt_version"] == __version__


def test_describe_scoped_keeps_the_output_schema_and_its_validity():
    info = _describe_scoped("info")["commands"]["info"]
    jsonschema.Draft202012Validator.check_schema(info["output"]["success"])
    jsonschema.Draft202012Validator.check_schema(info["output"]["error"])
