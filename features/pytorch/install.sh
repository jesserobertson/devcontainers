#!/bin/bash
set -e

su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev \
    --channel pytorch \
    --channel nvidia \
    --channel conda-forge \
    pytorch torchvision "pytorch-cuda=12.4" marimo'
