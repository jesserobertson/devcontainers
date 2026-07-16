#!/bin/bash
set -e

su dev -c '/home/dev/.pixi/bin/pixi global install --environment dev --channel conda-forge \
    ruff mypy pytest pytest-cov \
    mkdocs mkdocs-material mkdocstrings mkdocstrings-python'
