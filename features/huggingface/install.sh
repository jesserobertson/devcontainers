#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    huggingface_hub tokenizers
