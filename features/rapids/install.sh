#!/bin/bash
set -e

pixi global install --environment dev \
    --channel conda-forge \
    --channel rapidsai \
    --channel nvidia \
    cudf

# polars GPU extras are only on the NVIDIA PyPI index
"$HOME/.pixi/envs/dev/bin/pip" install "polars[gpu]>=1.0" \
    --extra-index-url https://pypi.nvidia.com
