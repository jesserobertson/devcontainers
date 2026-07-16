#!/bin/bash
set -e

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge \
    fastapi pydantic pydantic-settings uvicorn httpx'
