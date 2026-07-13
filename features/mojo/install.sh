#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev \
    --channel "https://conda.modular.com/max-nightly/" \
    --channel conda-forge \
    modular'
