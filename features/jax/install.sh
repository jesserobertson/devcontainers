#!/bin/bash
set -e

# Two different pixi roots below, deliberately: `/home/dev/.pixi/bin/pixi` is the
# binary's fixed install location, set before this repo's dotfiles apply PIXI_HOME.
# `/home/dev/.local/share/pixi/envs/dev` is where PIXI_HOME (set by those dotfiles)
# actually redirects pixi's *environments* at runtime. Making these paths consistent
# would break installs — verified the hard way in real-container debugging.
su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev --channel conda-forge \
    marimo pip'

# jax CUDA 12 variant is only available via PyPI
su dev -c '/home/dev/.local/share/pixi/envs/dev/bin/pip install "jax[cuda12]>=0.4"'
