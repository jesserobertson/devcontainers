#!/bin/bash
set -e

pixi global install --environment dev \
    --channel "https://conda.modular.com/max-nightly/" \
    --channel conda-forge \
    modular
