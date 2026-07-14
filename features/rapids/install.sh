#!/bin/bash
set -e

su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev \
    --channel conda-forge \
    --channel rapidsai \
    --channel nvidia \
    cudf pip'

# polars GPU extras are only on the NVIDIA PyPI index
su dev -c '/home/dev/.local/share/pixi/envs/dev/bin/pip install "polars[gpu]>=1.0" \
    --extra-index-url https://pypi.nvidia.com'
