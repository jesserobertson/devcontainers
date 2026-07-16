#!/bin/bash
set -e

# `su dev -c` resets PATH (doesn't inherit the interactive/dotfiles PATH), so pixi
# and its envs must be invoked by fully-qualified path — both rooted at PIXI_HOME
# (base/Dockerfile sets it explicitly to /home/dev/.local/share/pixi before installing
# pixi, matching this repo's dotfiles so no bare ~/.pixi dir is ever created).
su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge \
    marimo pip'

# jax CUDA 12 variant is only available via PyPI
su dev -c '/home/dev/.local/share/pixi/envs/dev/bin/pip install "jax[cuda12]>=0.4"'
