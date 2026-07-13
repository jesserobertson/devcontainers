#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    marimo altair vega_datasets
