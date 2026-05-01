#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["https://conda.modular.com/max-nightly/", "conda-forge"]
name = "mojo"
platforms = ["linux-64"]
version = "0.1.0"

[dependencies]
python = ">=3.11,<3.14"
modular = "*"
TOML
fi

pixi install --manifest-path "$MANIFEST"
