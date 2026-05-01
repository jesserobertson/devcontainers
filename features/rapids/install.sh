#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge", "rapidsai"]
name = "rapids"
platforms = ["linux-64"]
version = "0.1.0"

[system-requirements]
cuda = "12.8"

[dependencies]
python = ">=3.11,<3.13"

[pypi-options]
extra-index-urls = ["https://pypi.nvidia.com"]

[pypi-dependencies]
cudf-cu12 = ">=26,<27"
polars = { version = ">=1.0", extras = ["gpu"] }
TOML
fi

pixi install --manifest-path "$MANIFEST"
