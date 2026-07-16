#!/bin/bash
set -e

su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev --channel conda-forge \
    huggingface_hub tokenizers'
