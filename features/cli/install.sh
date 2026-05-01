#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge"]
name = "cli"
platforms = ["linux-64"]
version = "0.1.0"

[dependencies]
python = ">=3.11,<3.13"
typer = ">=0.9"
rich = ">=13.0"
pydantic = ">=2.0"
pydantic-settings = ">=2.0"
TOML
fi

pixi install --manifest-path "$MANIFEST"
