#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    marimo

# jax CUDA 12 variant is only available via PyPI
"$HOME/.pixi/envs/dev/bin/pip" install "jax[cuda12]>=0.4"
