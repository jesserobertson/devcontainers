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

# Homebrew's Linux installer always installs to the hardcoded prefix
# /home/linuxbrew/.linuxbrew regardless of which user runs it. That
# directory no longer matches dev's home (/home/dev), and /home itself is
# root-owned, so pre-create it and hand ownership to dev before running the
# installer as dev - without this, the installer would need sudo to create
# it, which dev intentionally does not have.
RUN mkdir -p /home/linuxbrew && chown dev:dev /home/linuxbrew
RUN su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/dev/.local/bin:$PATH"
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
# slim — core with nothing added. This is what base-ubuntu-slim publishes.
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS slim

# ─────────────────────────────────────────────────────────────────────────────
# full — core + Homebrew CLI bundle + pixi + pixi shell-hooks + fish login
# shell. base-ubuntu builds this (--target full); base-cuda builds it with a
# CUDA BASE_IMAGE. Behaviourally identical to the pre-multi-stage Dockerfile.
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS full
USER root

RUN su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

# The devcontainers/dotfiles repo redirects PIXI_HOME to ~/.local/share/pixi
# (avoiding a bare ~/.pixi dir, same as CARGO_HOME/RUSTUP_HOME) via a regular
# dotfiles config file - but Docker RUN steps don't source shell profiles,
# and chezmoi apply already ran back in the core stage. Set it explicitly so
# pixi's installer places the binary at the same path every other pixi
# invocation in this image (including every feature's install.sh) looks for.
ENV PIXI_HOME="/home/dev/.local/share/pixi"
RUN curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash

ENV PATH="/home/dev/.local/share/pixi/bin:$PATH"
ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish

RUN chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev && \
    mkdir -p /home/dev/.config/fish/conf.d && \
    printf 'if status is-interactive\n    if test -f /workspace/pixi.toml; or test -f /workspace/pyproject.toml\n        eval (pixi shell-hook --manifest-path /workspace --shell fish)\n    end\nend\n' \
    > /home/dev/.config/fish/conf.d/project-pixi.fish && \
    echo 'if [ -f /workspace/pixi.toml ] || [ -f /workspace/pyproject.toml ]; then eval "$(pixi shell-hook --manifest-path /workspace --shell bash)"; fi' >> /home/dev/.bashrc && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev
