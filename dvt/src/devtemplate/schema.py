from __future__ import annotations

import json
from importlib import resources
from typing import Any

import jsonschema

_SCHEMA: dict[str, Any] = json.loads(
    resources.files("devtemplate.schemas")
    .joinpath("devContainer.base.schema.json")
    .read_text(encoding="utf-8")
)


def validate_devcontainer_config(data: dict[str, Any]) -> None:
    """Validate data against the official devcontainer.json base schema.

    Raises jsonschema.ValidationError if data doesn't conform. Vendored schema
    source: https://github.com/devcontainers/spec/blob/main/schemas/devContainer.base.schema.json
    """
    jsonschema.validate(instance=data, schema=_SCHEMA)
