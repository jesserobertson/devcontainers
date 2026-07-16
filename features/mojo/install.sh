#!/bin/bash
set -e

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev \
    --channel "https://conda.modular.com/max-nightly/" \
    --channel conda-forge \
    modular'
