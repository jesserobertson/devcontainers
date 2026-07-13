#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    marimo'

# jax CUDA 12 variant is only available via PyPI
su dev -c '/home/dev/.pixi/envs/dev/bin/pip install "jax[cuda12]>=0.4"'
