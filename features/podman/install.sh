#!/bin/bash
set -euo pipefail

# podman itself; podman-docker for a drop-in `docker` CLI shim (this repo's
# own test suite shells out to `docker` directly - see
# tests/features/test_agent.py); podman-compose so podman-docker's postinst
# wires up a `docker compose` plugin shim too (examples/ollama-sidecar's
# tests use `docker compose ...`); uidmap for newuidmap/newgidmap (rootless
# UID mapping); slirp4netns for rootless networking.
apt-get update && apt-get install -y podman podman-docker podman-compose uidmap slirp4netns

# Rootless podman needs a subordinate UID/GID range to map container UIDs
# into. useradd only assigns one automatically when uidmap is already
# installed at user-creation time - it wasn't: base/Dockerfile creates dev
# long before this feature runs. Idempotent: a rebuild re-running this
# feature must not error on an already-present range.
grep -q "^dev:" /etc/subuid || usermod --add-subuids 100000-165535 dev
grep -q "^dev:" /etc/subgid || usermod --add-subgids 100000-165535 dev

# Force the vfs storage driver, not overlay/fuse-overlayfs: overlay needs
# /dev/fuse (or a real overlay-capable rootless kernel path) passed through
# from the outer container's own runtime via runArgs, which this feature
# deliberately doesn't require every consumer to add. vfs is slower and
# uses more disk (no copy-on-write) but works unmodified inside any plain,
# unprivileged container. A consumer that wants overlay speed can still add
# --device=/dev/fuse to their own runArgs and override this file.
mkdir -p /home/dev/.config/containers
cat > /home/dev/.config/containers/storage.conf <<'EOF'
[storage]
driver = "vfs"
EOF
chown -R dev:dev /home/dev/.config/containers
