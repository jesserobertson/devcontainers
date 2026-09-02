#!/bin/bash
set -e

# Idempotency guard: base-ubuntu / base-cuda already carry brew (this feature
# is baked into them). Re-running the installer there would be wasted work at
# best; skip cleanly so the feature is safe to list explicitly on a bundle
# base too.
if [ -x /home/linuxbrew/.linuxbrew/bin/brew ]; then
    echo "Homebrew already present at /home/linuxbrew/.linuxbrew - skipping."
    exit 0
fi

# Homebrew's Linux installer always targets the hardcoded prefix
# /home/linuxbrew/.linuxbrew regardless of which user runs it. That path is
# not dev's home and /home is root-owned, so pre-create it and hand it to dev
# before running the installer as dev (dev has no sudo).
mkdir -p /home/linuxbrew
chown dev:dev /home/linuxbrew
su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
