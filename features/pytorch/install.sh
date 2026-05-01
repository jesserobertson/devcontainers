#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["pytorch", "nvidia", "conda-forge"]
name = "pytorch"
platforms = ["linux-64"]
version = "0.1.0"

[tasks]
marimo = "marimo edit --host 0.0.0.0 --port 2718 --no-token"

[system-requirements]
cuda = "12.8"

[dependencies]
python = ">=3.11,<3.13"
pytorch = ">=2.0"
torchvision = "*"
pytorch-cuda = "12.4.*"
marimo = ">=0.21.1,<0.22"
TOML
fi

pixi install --manifest-path "$MANIFEST"
