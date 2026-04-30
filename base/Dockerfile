ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
    build-essential procps curl file git wget unzip sudo

# Homebrew refuses to run as root; install and use it as a dedicated user
RUN useradd -m -s /bin/bash linuxbrew && \
    echo 'linuxbrew ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
RUN su linuxbrew -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$PATH"
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_NO_ANALYTICS=1

RUN su linuxbrew -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

RUN curl -fsSL https://pixi.sh/install.sh | bash
RUN curl -fsLS get.chezmoi.io | sh -s -- -b /usr/local/bin
RUN GIT_TERMINAL_PROMPT=0 chezmoi init --apply --no-tty --exclude=scripts \
    https://github.com/jesserobertson/dotfiles.git

ENV PATH="/root/.pixi/bin:/root/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=300
ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish

RUN chsh -s /home/linuxbrew/.linuxbrew/bin/fish root && \
    printf 'detached-environments = "/opt/pixi-envs"\n' > /root/.pixi/config.toml && \
    git config --global --add safe.directory /workspace && \
    mkdir -p /root/.config/fish/conf.d && \
    printf 'if status is-interactive\n    eval (pixi shell-hook --manifest-path /workspace/pixi.toml --shell fish)\nend\n' \
    > /root/.config/fish/conf.d/project-pixi.fish && \
    echo 'eval "$(pixi shell-hook --manifest-path /workspace/pixi.toml --shell bash)"' >> /root/.bashrc

HEALTHCHECK --interval=5s --timeout=5s --start-period=10s --retries=3 \
  CMD bash -c 'exit 0'

WORKDIR /workspace
