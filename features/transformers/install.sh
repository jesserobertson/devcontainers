#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    transformers datasets accelerate tokenizers
