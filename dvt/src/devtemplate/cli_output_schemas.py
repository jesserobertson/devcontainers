"""Pydantic models describing every JSON-capable command's output shape.

Used two ways: `dvt --describe` calls `.model_json_schema()` on each to
publish real JSON Schema (see devtemplate.cli_support.describe_app), and
tests call `.model_validate()` (or validate a real --json invocation's
output against the *published* schema directly, via the `jsonschema`
package) to prove a command's actual output still matches what's declared
here - so the two can't silently drift apart the way a hand-maintained,
never-checked description would.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, RootModel

__all__ = [
    "OUTPUT_MODELS",
    "DeleteOutput",
    "ErrorOutput",
    "FeatureAddOutput",
    "FeatureInfo",
    "FeatureListOutput",
    "FeatureRemoveOutput",
    "FeatureShowOutput",
    "FeatureSyncOutput",
    "ImageCreateOutput",
    "ImageDeleteOutput",
    "ImageListOutput",
    "ImageShowOutput",
    "ImageSyncOutput",
    "ImageUpdateOutput",
    "InfoOutput",
    "InitOutput",
    "ProjectInfo",
    "StopOutput",
    "UpOutput",
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
    fixed dvt-defined contract."""


class FeatureSyncOutput(BaseModel):
    ok: Literal[True]
    synced: list[str]


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


class ImageSyncOutput(BaseModel):
    ok: Literal[True]
    synced: list[str]


class ImageCreateOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class ImageUpdateOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class ImageDeleteOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "init": InitOutput,
    "up": UpOutput,
    "info": InfoOutput,
    "stop": StopOutput,
    "delete": DeleteOutput,
    "feature list": FeatureListOutput,
    "feature show": FeatureShowOutput,
    "feature sync": FeatureSyncOutput,
    "feature add": FeatureAddOutput,
    "feature remove": FeatureRemoveOutput,
    "image list": ImageListOutput,
    "image show": ImageShowOutput,
    "image sync": ImageSyncOutput,
    "image create": ImageCreateOutput,
    "image update": ImageUpdateOutput,
    "image delete": ImageDeleteOutput,
}
"""Keyed the same way describe_app keys its "commands" dict (dotted names
for feature subcommands), so devtemplate.cli_support._describe_command can
look a command's output model up by the same full_name it already has."""
