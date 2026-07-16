#!/bin/bash
set -e

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge \
    marimo altair vega_datasets'
