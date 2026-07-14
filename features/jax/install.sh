#!/bin/bash
set -e

su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev --channel conda-forge \
    marimo pip'

# jax CUDA 12 variant is only available via PyPI
su dev -c '/home/dev/.local/share/pixi/envs/dev/bin/pip install "jax[cuda12]>=0.4"'
