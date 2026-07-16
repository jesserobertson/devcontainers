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

# Egress-allowlist firewall: root-owned, root-only. dev can only run it via
# the scoped sudoers rule below, never edit or replace it.
install -m 0700 -o root -g root "$SCRIPT_DIR/init-firewall.sh" /usr/local/bin/init-firewall.sh

# vibe: opt-in unattended auto-mode wrapper, owned by dev.
mkdir -p /home/dev/.local/bin
install -m 0755 -o dev -g dev "$SCRIPT_DIR/vibe" /home/dev/.local/bin/vibe
chown dev:dev /home/dev/.local/bin

# The only sudo access dev ever gets: this one script, nothing else.
echo 'dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh' > /etc/sudoers.d/claude-agent
chmod 0440 /etc/sudoers.d/claude-agent
