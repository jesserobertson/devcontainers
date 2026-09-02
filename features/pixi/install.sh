#!/bin/bash
set -e

# The dotfiles repo redirects PIXI_HOME to ~/.local/share/pixi via a plain
# config file, but Docker/feature RUN steps don't source shell profiles and
# chezmoi apply already ran in the base image. Set it explicitly so the
# installer places the binary where every other pixi invocation (every
# feature's install.sh, the shell hooks) looks for it.
export PIXI_HOME="/home/dev/.local/share/pixi"
curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash

if [ "${SHELLHOOK:-auto}" = "auto" ]; then
    # bash: always. fish: only if fish is installed (shell-kit present).
    su dev -c 'echo "if [ -f /workspace/pixi.toml ] || [ -f /workspace/pyproject.toml ]; then eval \"\$(pixi shell-hook --manifest-path /workspace --shell bash)\"; fi" >> /home/dev/.bashrc'
    if [ -x /home/linuxbrew/.linuxbrew/bin/fish ]; then
        su dev -c 'mkdir -p /home/dev/.config/fish/conf.d && printf "if status is-interactive\n    if test -f /workspace/pixi.toml; or test -f /workspace/pyproject.toml\n        eval (pixi shell-hook --manifest-path /workspace --shell fish)\n    end\nend\n" > /home/dev/.config/fish/conf.d/project-pixi.fish'
    fi
fi

if [ -n "${GLOBAL:-}" ]; then
    su dev -c "/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge ${GLOBAL}"
fi
