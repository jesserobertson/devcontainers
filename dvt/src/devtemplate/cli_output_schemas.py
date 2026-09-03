"""Pydantic models describing every JSON-capable command's output shape.

Used two ways: `dvt --describe` calls `.model_json_schema()` on each to
publish real JSON Schema (via the `attach_output_schema` enrich hook wired
into devtemplate.describe), and tests call `.model_validate()` (or validate
a real --json invocation's output against the *published* schema directly,
via the `jsonschema` package) to prove a command's actual output still
matches what's declared here - so the two can't silently drift apart the
way a hand-maintained, never-checked description would.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, RootModel

__all__ = [
    "OUTPUT_MODELS",
    "DeleteOutput",
    "ErrorOutput",
    "FeatureAddOutput",
    "FeatureDepsOutput",
    "FeatureInfo",
    "FeatureListOutput",
    "FeatureRemoveOutput",
    "FeatureShowOutput",
    "ImageListOutput",
    "ImageSetOutput",
    "ImageShowOutput",
    "ImageUnsetOutput",
    "InfoOutput",
    "InitOutput",
    "ProjectInfo",
    "StopOutput",
    "SyncOutput",
    "UpOutput",
    "attach_output_schema",
]


class ErrorOutput(BaseModel):
    """Shared failure shape for every JSON-capable command (see
    devtemplate.cli_support.report_error/unwrap_or_exit)."""

    ok: Literal[False]
    error: str


class InitOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class UpOutput(BaseModel):
    ok: Literal[True]
    name: str


class FeatureInfo(BaseModel):
    """One installed feature's name plus its cached template description -
    empty when the name isn't a cached template (e.g. an untracked feature
    read straight from devcontainer.json's own "features" map, which is
    keyed by OCI ref rather than dvt's template cache name)."""

    name: str
    description: str


class ProjectInfo(BaseModel):
    name: str | None
    path: str
    image: str | None
    features: list[FeatureInfo]
    features_tracked: bool


class InfoOutput(BaseModel):
    ok: Literal[True]
    project: ProjectInfo
    runtime_reachable: bool
    # Shape varies with status ("not_found" | "<container status>" |
    # "multiple") - kept loose deliberately; a consumer branches on
    # workspace["status"] the same way devtemplate.commands.info's own
    # human-readable output does, rather than this modeling every variant.
    workspace: dict[str, Any] | None


class StopOutput(BaseModel):
    ok: Literal[True]
    name: str


class DeleteOutput(BaseModel):
    ok: Literal[True]
    name: str


class FeatureListOutput(RootModel[list[dict[str, Any]]]):
    """No {"ok": ...} envelope - predates that convention and kept as-is,
    see commands.md's "Machine-readable output" section."""


class FeatureShowOutput(RootModel[dict[str, Any]]):
    """Raw pass-through of the cached feature's own devcontainer.json
    overlay - shape is whatever that feature's template contains, not a
    fixed dvt-defined contract. When the feature is in the local cache,
    `dvt feature show --json` also adds an optional `resolved_depends_on`
    key: the transitive `dependsOn` closure as a list of feature ids."""


class FeatureDepsOutput(RootModel[dict[str, Any]]):
    """`{"<feature id>": {"pulls_in": [...], "installs_after": [...]}, ...}` -
    one entry per selected feature. Kept permissive (like FeatureListOutput /
    FeatureShowOutput) rather than a fixed per-entry model."""


class SyncOutput(BaseModel):
    ok: Literal[True]
    features: list[str]
    images: list[str]
    feature_specs: list[str]


class FeatureAddOutput(BaseModel):
    ok: Literal[True]
    added: list[str]


class FeatureRemoveOutput(BaseModel):
    ok: Literal[True]
    removed: list[str]


class ImageListOutput(RootModel[list[dict[str, Any]]]):
    """No {"ok": ...} envelope, matching FeatureListOutput's convention."""


class ImageShowOutput(RootModel[dict[str, Any]]):
    """Raw pass-through of the cached image's own metadata JSON."""


class ImageSetOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class ImageUnsetOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "init": InitOutput,
    "up": UpOutput,
    "info": InfoOutput,
    "stop": StopOutput,
    "delete": DeleteOutput,
    "sync": SyncOutput,
    "feature list": FeatureListOutput,
    "feature show": FeatureShowOutput,
    "feature deps": FeatureDepsOutput,
    # `feature tree` is the hidden alias of `feature deps` (same callback, same
    # --json payload); describe_app walks it as a distinct command, so it needs
    # its own declared output shape too or the manifest carries a --json command
    # with none (violating the invariant in docs/content/commands.md).
    "feature tree": FeatureDepsOutput,
    "feature add": FeatureAddOutput,
    "feature remove": FeatureRemoveOutput,
    "image list": ImageListOutput,
    "image show": ImageShowOutput,
    "image set": ImageSetOutput,
    "image unset": ImageUnsetOutput,
}
"""Keyed the same way describe_app keys its "commands" dict (dotted names
for feature subcommands), so attach_output_schema can look a command's
output model up by the same full_name it already has."""


def attach_output_schema(full_name: str, entry: dict[str, Any]) -> None:
    """Enrich hook for devtemplate.describe: for any command that has a
    --json mode, add its output shape as real JSON Schema - `output.success`
    from the command's own model, `output.error` from the shared error
    shape - to that command's --describe manifest entry, in place."""
    model = OUTPUT_MODELS.get(full_name)
    if model is not None:
        entry["output"] = {
            "success": model.model_json_schema(),
            "error": ErrorOutput.model_json_schema(),
        }
