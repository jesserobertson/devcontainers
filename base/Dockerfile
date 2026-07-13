ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
    build-essential procps curl file git wget unzip sudo

# One dedicated non-root user for the whole image. Homebrew refuses to run
# as root anyway, and containment for features/claude-agent requires a
# non-root runtime user regardless. No passwordless sudo: the boundary
# must hold even if this user's session is compromised — see
# features/claude-agent for the one narrowly-scoped exception it adds for
# itself, nothing else.
RUN useradd -m -s /bin/bash dev

# Homebrew's Linux installer always installs to the hardcoded prefix
# /home/linuxbrew/.linuxbrew regardless of which user runs it. That
# directory no longer matches dev's home (/home/dev), and /home itself is
# root-owned, so pre-create it and hand ownership to dev before running the
# installer as dev - without this, the installer would need sudo to create
# it, which dev intentionally does not have.
RUN mkdir -p /home/linuxbrew && chown dev:dev /home/linuxbrew
RUN su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$PATH"
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_NO_ANALYTICS=1

RUN su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

RUN curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash
RUN curl -fsLS get.chezmoi.io | sh -s -- -b /usr/local/bin
RUN su dev -c 'GIT_TERMINAL_PROMPT=0 chezmoi init --apply --no-tty --exclude=scripts \
    https://github.com/jesserobertson/dotfiles.git'

ENV PATH="/home/dev/.pixi/bin:/home/dev/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=300
ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish

# Shared pixi env store: features install as root (devcontainer-feature
# spec guarantee) but must leave envs usable by dev at runtime.
RUN mkdir -p /opt/pixi-envs && chown dev:dev /opt/pixi-envs

RUN chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev && \
    HOME=/home/dev git config --global --add safe.directory /workspace && \
    printf 'detached-environments = "/opt/pixi-envs"\n' > /home/dev/.pixi/config.toml && \
    mkdir -p /home/dev/.config/fish/conf.d && \
    printf 'if status is-interactive\n    eval (pixi shell-hook --manifest-path /workspace/pixi.toml --shell fish)\nend\n' \
    > /home/dev/.config/fish/conf.d/project-pixi.fish && \
    echo 'eval "$(pixi shell-hook --manifest-path /workspace/pixi.toml --shell bash)"' >> /home/dev/.bashrc && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev
