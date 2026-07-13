#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    typer rich pydantic pydantic-settings
