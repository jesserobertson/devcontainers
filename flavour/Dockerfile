FROM ghcr.io/jesserobertson/base-cuda:latest

COPY pixi.toml /workspace/pixi.toml
RUN pixi install --manifest-path /workspace/pixi.toml

WORKDIR /workspace
