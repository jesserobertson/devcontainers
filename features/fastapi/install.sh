#!/bin/bash
set -e

MANIFEST="/workspace/pixi.toml"

if [ ! -f "$MANIFEST" ]; then
    cat > "$MANIFEST" << 'TOML'
[workspace]
channels = ["conda-forge"]
name = "fastapi"
platforms = ["linux-64"]
version = "0.1.0"

[tasks]
serve = "uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

[dependencies]
python = ">=3.11,<3.13"
fastapi = ">=0.110"
pydantic = ">=2.0"
pydantic-settings = ">=2.0"
uvicorn = ">=0.27"
httpx = ">=0.27"
TOML
fi

pixi install --manifest-path "$MANIFEST"
