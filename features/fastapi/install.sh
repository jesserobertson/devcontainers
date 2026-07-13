#!/bin/bash
set -e

pixi global install --environment dev --channel conda-forge \
    fastapi pydantic pydantic-settings uvicorn httpx
