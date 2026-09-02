#!/bin/bash
set -e
set -o pipefail

# The dotfiles repo redirects PIXI_HOME to ~/.local/share/pixi via a plain
# config file, but Docker/feature RUN steps don't source shell profiles and
# chezmoi apply already ran in the base image. Set it explicitly so the
# installer places the binary where every other pixi invocation (every
# feature's install.sh, the shell hooks) looks for it.
export PIXI_HOME="/home/dev/.local/share/pixi"

# Idempotency guard: base-ubuntu / base-cuda already carry pixi (this feature
# is baked into them). Re-running the installer there would be wasted work at
# best; skip it so the feature is safe to list explicitly on a bundle base too.
if [ ! -x /home/dev/.local/share/pixi/bin/pixi ]; then
    curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash
fi
[ -x /home/dev/.local/share/pixi/bin/pixi ] || { echo "pixi install failed" >&2; exit 1; }

if [ "${SHELLHOOK:-auto}" = "auto" ]; then
    # bash: always. fish: only if fish is installed (shell-kit present).
    # Both writes are guarded so a re-run on an already-provisioned base is a no-op.
    grep -qsF 'pixi shell-hook --manifest-path /workspace' /home/dev/.bashrc || \
        su dev -c 'echo "if [ -f /workspace/pixi.toml ] || [ -f /workspace/pyproject.toml ]; then eval \"\$(pixi shell-hook --manifest-path /workspace --shell bash)\"; fi" >> /home/dev/.bashrc'
    [ -f /home/dev/.config/fish/conf.d/project-pixi.fish ] || \
    if [ -x /home/linuxbrew/.linuxbrew/bin/fish ]; then
        su dev -c 'mkdir -p /home/dev/.config/fish/conf.d && printf "if status is-interactive\n    if test -f /workspace/pixi.toml; or test -f /workspace/pyproject.toml\n        eval (pixi shell-hook --manifest-path /workspace --shell fish)\n    end\nend\n" > /home/dev/.config/fish/conf.d/project-pixi.fish'
    fi
fi

if [ -n "${GLOBAL:-}" ]; then
    su dev -c "/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge ${GLOBAL}"
fi
