#!/bin/bash
set -e

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge \
    ruff mypy pytest pytest-cov \
    mkdocs mkdocs-material mkdocstrings mkdocstrings-python \
    helix pyright'

# Helix's own built-in default Python language-server config expects pylsp,
# which this feature doesn't install - point it at ruff's native LSP (`ruff
# server`, ships in the same ruff binary above - no separate ruff-lsp
# package needed) for lint/format-on-save, and pyright for hover/completion/
# go-to-definition. Global user config, not project-level: this feature
# installs at image-build time, before any consuming project's own files
# (or a project-level .helix/) exist.
mkdir -p /home/dev/.config/helix
cat > /home/dev/.config/helix/languages.toml <<'EOF'
[language-server.ruff]
command = "ruff"
args = ["server"]

[language-server.pyright]
command = "pyright-langserver"
args = ["--stdio"]

[[language]]
name = "python"
language-servers = ["pyright", "ruff"]
EOF
chown -R dev:dev /home/dev/.config/helix
