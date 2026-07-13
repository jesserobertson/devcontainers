#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    huggingface_hub tokenizers'
