#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# init-firewall.sh's runtime dependencies (iproute2 for `ip route`, used to
# detect the host network), plus sudo itself: this feature's whole point is
# the scoped sudoers rule below, so it must not silently depend on the
# consuming image having already installed sudo.
apt-get update && apt-get install -y iptables ipset dnsutils jq aggregate iproute2 sudo

# Claude Code itself: native standalone binary, installed into dev's home.
curl -fsSL https://claude.ai/install.sh | su dev -s /bin/bash

# Node.js: pi's installer needs it when run non-interactively (confirmed
# directly - "No terminal detected; install Node.js 22.19.0 or newer and
# npm, then run this installer again" - install.sh always runs non-TTY).
# Installed via pixi (fully-qualified path - su dev -c doesn't inherit
# PATH), matching every other pixi install in this ecosystem.
su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge "nodejs>=22.19"'

# pi's `npm install -g` puts its shim inside the pixi env's own bin dir by
# default, which isn't on PATH anywhere (pixi only exposes binaries it
# installed itself - node/npm/npx - not things npm subsequently adds in that
# same env). Redirect npm's global prefix to ~/.local first, unconditionally
# on PATH via base/Dockerfile, and what omp's own installer already uses by
# default - so both land somewhere actually reachable. Fully-qualified path:
# `su dev -c` resets PATH to a bare minimum (same reason pixi itself needs
# one above), so bare `npm` would not resolve here.
su dev -c '/home/dev/.local/share/pixi/bin/npm config set prefix "$HOME/.local"'

# Pi and oh-my-pi (omp): alternative agent harnesses, usable against the same
# Anthropic account as Claude Code (built-in provider, reads ANTHROPIC_API_KEY)
# or a local model (see devcontainers' host-services/ollama). Only omp gets a
# vibe-* auto-mode wrapper below - vanilla pi has no built-in unattended/
# auto-approve mode (confirmed: no such flag exists in its CLI), omp adds one.
# Unlike claude/omp's installers (which just write a binary and don't need to
# look anything up), pi's installer internally runs `npm install -g`, so it
# needs npm's shim dir on PATH - `su dev -s /bin/bash` alone doesn't
# guarantee that (same PATH-reset behaviour as `su dev -c`), so prepend it
# explicitly for this one invocation.
curl -fsSL https://pi.dev/install.sh | su dev -c 'PATH="/home/dev/.local/share/pixi/bin:$PATH" bash'
curl -fsSL https://omp.sh/install | su dev -s /bin/bash

# Egress-allowlist firewall: root-owned, root-only. dev can only run it via
# the scoped sudoers rule below, never edit or replace it.
install -m 0700 -o root -g root "$SCRIPT_DIR/init-firewall.sh" /usr/local/bin/init-firewall.sh

# vibe: opt-in unattended auto-mode wrapper, owned by dev. `vibe` runs
# oh-my-pi (the default day-to-day agent), `vibe claude` runs Claude Code -
# one entrypoint for both.
mkdir -p /home/dev/.local/bin
install -m 0755 -o dev -g dev "$SCRIPT_DIR/vibe" /home/dev/.local/bin/vibe
chown dev:dev /home/dev/.local/bin

# The only sudo access dev ever gets: this one script, nothing else.
echo 'dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh' > /etc/sudoers.d/claude-agent
chmod 0440 /etc/sudoers.d/claude-agent
