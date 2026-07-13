#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    ruff mypy pytest pytest-cov \
    mkdocs mkdocs-material mkdocstrings mkdocstrings-python'
