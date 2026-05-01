#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge"]
name = "jax"
platforms = ["linux-64"]
version = "0.1.0"

[tasks]
marimo = "marimo edit --host 0.0.0.0 --port 2718 --no-token"

[system-requirements]
cuda = "12.8"

[dependencies]
python = ">=3.11,<3.13"
marimo = ">=0.21.1,<0.22"

[pypi-dependencies]
jax = { version = ">=0.4", extras = ["cuda12"] }
TOML
fi

pixi install --manifest-path "$MANIFEST"
