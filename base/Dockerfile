ARG BASE_IMAGE=ubuntu:24.04

# ─────────────────────────────────────────────────────────────────────────────
# core — shared by every variant. No pixi, no Homebrew CLI bundle, no fish.
# Published (as-is) as base-ubuntu-slim via the `slim` stage below.
# ─────────────────────────────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS core

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
    build-essential procps curl file git wget unzip sudo

# One dedicated non-root user for the whole image. Homebrew refuses to run
# as root anyway, and containment for features/agent requires a non-root
# runtime user regardless. No passwordless sudo: the boundary must hold
# even if this user's session is compromised — see features/agent for the
# one narrowly-scoped exception it adds for itself, nothing else.
RUN useradd -m -s /bin/bash dev

ENV PATH="/home/dev/.local/bin:$PATH"
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_NO_ANALYTICS=1
ENV UV_HTTP_TIMEOUT=300

# Dotfiles land in core (not full) because they degrade gracefully when the
# CLI bundle and the Python toolchain aren't present:
# dot_config/fish/functions/init_cached.fish and dot_bashrc.tmpl's
# _init_cached both `command -v` each tool and no-op when it's missing, and
# PATH additions are existence-checked. `--exclude=scripts` means no run_*
# install script executes here.
RUN curl -fsLS get.chezmoi.io | sh -s -- -b /usr/local/bin
RUN su dev -c 'GIT_TERMINAL_PROMPT=0 chezmoi init --apply --no-tty --exclude=scripts \
    https://github.com/jesserobertson/dotfiles.git'

RUN HOME=/home/dev git config --global --add safe.directory /workspace && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev

# ─────────────────────────────────────────────────────────────────────────────
# slim — core with nothing added. base-ubuntu-slim / base-cuda-slim publish this.
# base-ubuntu / base-cuda are assembled from this image + the homebrew,
# shell-kit and pixi features via `devcontainer build` (see images/).
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS slim
USER dev
