#!/bin/bash
set -e

pixi global install --environment dev \
    --channel pytorch \
    --channel nvidia \
    --channel conda-forge \
    pytorch torchvision "pytorch-cuda=12.4" marimo
