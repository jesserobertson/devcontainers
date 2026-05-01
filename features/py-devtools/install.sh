#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge"]
name = "py-devtools"
platforms = ["linux-64"]
version = "0.1.0"

[dependencies]
python = ">=3.11,<3.13"
ruff = ">=0.4"
mypy = ">=1.10"
pytest = ">=8.0"
pytest-cov = ">=5.0"
mkdocs = ">=1.6"
mkdocs-material = ">=9.5"
mkdocstrings = ">=0.25"
mkdocstrings-python = ">=1.10"
TOML
fi

pixi install --manifest-path "$MANIFEST"
