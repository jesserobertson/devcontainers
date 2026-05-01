#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge"]
name = "marimo"
platforms = ["linux-64"]
version = "0.1.0"

[tasks]
marimo = "marimo edit --host 0.0.0.0 --port 2718 --no-token"

[dependencies]
python = ">=3.11,<3.13"
marimo = ">=0.21.1,<0.22"
altair = ">=5.0"
vega_datasets = "*"
TOML
fi

pixi install --manifest-path "$MANIFEST"
