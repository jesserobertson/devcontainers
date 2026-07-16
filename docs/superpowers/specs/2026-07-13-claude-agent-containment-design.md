# Claude Agent Containment Design

**Date:** 2026-07-13
**Status:** Approved

## Overview

A `claude-agent` devcontainer feature that lets Claude Code run in an opt-in, unattended
"auto mode" (`--dangerously-skip-permissions`) with the container itself — not model-layer
judgment — as the safety boundary. Motivated by
[Anthropic's containment writeup](https://www.anthropic.com/engineering/how-we-contain-claude):
when approval prompts are skipped, deterministic environment boundaries (non-root execution,
network egress allowlists, no host credentials) are the only defense that held up against a
real prompt-injection/credential-exfiltration attack — model-layer defenses did not.

This requires the shared base image to migrate from an all-root user model to a single
non-root `dev` user first, since containment is meaningless if the agent just runs as root.
`kidinnu` (`../kidinnu`) is the first project wired up to use it.

**Non-goal:** hardening the `ramalama` LLM sidecar image. It's an inference server, not an
agent execution environment, so it's out of scope for containment — it keeps running as root
(see Phase 1 below for the one-line consequence this has for it).

## Phase 1 — non-root base migration

`base/Dockerfile`:

- Rename the `linuxbrew` user to `dev` (brew already required a dedicated non-root user;
  this reuses it for everything rather than adding a third identity).
- Drop the `linuxbrew ALL=(ALL) NOPASSWD:ALL` sudoers line entirely. `dev` gets no sudo,
  full stop. This is a deliberate, repo-wide behavior change (no more ad hoc
  `sudo apt install` from an interactive shell in *any* project using this base image, not
  just agent ones) — the tradeoff accepted in favor of a real containment boundary. Any
  future privileged one-off setup (e.g. the firewall init in Phase 2) needs its own
  narrowly-scoped sudoers.d exception for that specific command, added by whichever
  feature needs it — never a blanket grant.
- Move pixi config (`detached-environments = "/opt/pixi-envs"`), chezmoi dotfiles apply,
  fish shell hook install, `chsh`, and `git config --global --add safe.directory /workspace`
  from `/root/...` to `/home/dev/...`, each invoked via `su dev -c '...'` (mirrors the
  existing brew invocation pattern).
- `chown dev:dev /opt/pixi-envs` so features installing into it (as root, see below) leave
  it usable by `dev` at runtime.
- End the Dockerfile with `USER dev`.

**Mechanical ripple effects** (same shape across all files, low-risk):

- Devcontainer features always execute `install.sh` as root regardless of `remoteUser`
  (a devcontainer-spec guarantee, not something we control) — so every existing
  `features/*/install.sh` wraps its `pixi global install ...` call in `su dev -c "..."`,
  and the two `"$HOME/.pixi/envs/dev/bin/pip" install ...` lines in `rapids`/`jax`
  become `su dev -c '/home/dev/.pixi/envs/dev/bin/pip install ...'`. Affected:
  `rapids`, `jax`, `pytorch`, `mojo`, `marimo`, `fastapi`, `cli`, `py-devtools`,
  `huggingface`, `transformers`, `ramalama`. The `ramalama` feature's
  `/etc/profile.d/ramalama.sh` write is unaffected (that part legitimately needs root
  and isn't a pixi call).
- All examples (`examples/ramalama-sidecar/.devcontainer/devcontainer.json`) and README
  snippets: `"remoteUser": "root"` → `"dev"`; pixi cache volume target
  `/root/.cache/pixi` → `/home/dev/.cache/pixi`.
- `ramalama/Dockerfile` (the sidecar image, `FROM base-cuda`): add an explicit
  `USER root` before its `apt-get install`/`pip install --break-system-packages` lines,
  since it now inherits `USER dev` from the migrated base. It intentionally stays root —
  it's not an agent-execution context, so the migration doesn't try to make it non-root too.
- `host-services/ramalama/docker-compose.yml` is untouched — it runs the upstream
  `quay.io/ramalama/cuda` image, not ours, so `/root/.local/share/ramalama` there is
  unrelated to this migration.

## Phase 2 — `features/claude-agent`

New feature, composes onto `base-ubuntu` or `base-cuda` like any other.

**`devcontainer-feature.json`:** id `claude-agent`, no options (intentionally minimal,
matching `huggingface`/`transformers`).

**`install.sh`** (runs as root, per feature spec):

- Installs the `claude` CLI via the native standalone installer into `dev`'s home:
  `curl -fsSL https://claude.ai/install.sh | su dev -s /bin/bash`. No Node/npm dependency.
- Installs `init-firewall.sh` to `/usr/local/bin/init-firewall.sh` (root-only, `0700`).
- Installs the `vibe` wrapper to `/home/dev/.local/bin/vibe` (owned by `dev`, executable).

**`init-firewall.sh`** — adapted from the reference implementation in
`anthropics/claude-code/.devcontainer/init-firewall.sh`: ipset + iptables, default-DROP
egress policy, allowlisting resolved IPs/CIDRs for only:

- GitHub (via `api.github.com/meta` IP ranges — covers `github.com`, `api.github.com`,
  git push/pull over HTTPS)
- `api.anthropic.com`, `claude.ai` (Claude Code itself, plus re-running the installer)
- `pypi.org`, `files.pythonhosted.org`, `conda.anaconda.org`, `repo.anaconda.com`
  (pixi/conda-forge + PyPI packages kidinnu depends on)

Keeps the reference script's self-test at the end (confirms `https://example.com` is
unreachable and `https://api.github.com/zen` is reachable; exits non-zero on either
failure so a broken firewall fails loudly rather than silently allowing everything or
blocking everything).

`init-firewall.sh` finishes (after its self-test passes) by touching a world-readable
marker file, `install -m 644 /dev/null /run/firewall-armed` equivalent
(`touch /run/firewall-armed && chmod 644 /run/firewall-armed`), so its armed/not-armed
state can be checked without needing `NET_ADMIN`/root privileges to query iptables directly.

**Sudo exception:** whether `postStartCommand` actually runs as `containerUser` (root) or
`remoteUser` is ambiguous in the devcontainer spec text, and Anthropic's own reference
`devcontainer.json` hedges against that ambiguity by explicitly running
`"postStartCommand": "sudo /usr/local/bin/init-firewall.sh"` — implying it cannot be
assumed to run as root. So `install.sh` adds one narrowly-scoped sudoers.d rule:

```
dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh
```

This is the *only* sudo access `dev` ever gets, added by the `claude-agent` feature
specifically (not a blanket grant) — it does not weaken the Phase 1 rule that the base
image itself ships with zero passwordless sudo. `install.sh` also sets `init-firewall.sh`
to root-owned, mode `0700`, so it cannot be edited or replaced by `dev` before being
sudo-run.

**`vibe`** wrapper script (run interactively as `dev`):

```bash
#!/bin/bash
set -e
if [ ! -f /run/firewall-armed ]; then
    echo "error: egress firewall is not armed — refusing to run in auto mode" >&2
    exit 1
fi
exec claude --dangerously-skip-permissions "$@"
```

Plain `claude` continues to run with normal approval prompts; `vibe` is the explicit,
one-word opt-in to unattended mode, and it refuses to start if the firewall didn't come up.

**Consuming `devcontainer.json` needs:**

```json
{
  "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
  "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
  "waitFor": "postStartCommand",
  "remoteUser": "dev",
  "mounts": [
    "source=claude-agent-config-${devcontainerId},target=/home/dev/.claude,type=volume",
    "source=claude-agent-bashhistory-${devcontainerId},target=/commandhistory,type=volume"
  ]
}
```

The two named volumes persist Claude's config and shell history across container
rebuilds, matching the upstream reference pattern.

## kidinnu wiring

New `kidinnu/.devcontainer/devcontainer.json`:

- `ghcr.io/jesserobertson/base-ubuntu:latest` (kidinnu is CPU-only per its `pixi.toml` —
  marimo/altair/jax without the CUDA extra — no GPU base needed).
- Features: `py-devtools` + `claude-agent`.
- `containerEnv.ANTHROPIC_API_KEY`: `${localEnv:ANTHROPIC_API_KEY}`.
- `containerEnv.GH_TOKEN` (or `GITHUB_TOKEN`): `${localEnv:KIDINNU_GH_TOKEN}` — a
  fine-grained GitHub PAT scoped to `jesserobertson/kidinnu` only (contents + pull-requests,
  no admin, no other-repo access). Never the host's `gh`/ssh credentials. Documented in the
  example's own README as an env var the user sets in their host shell profile — never
  committed.
- Standard `postCreateCommand: pixi install` / pixi cache volume, matching every other
  project in this repo.

## Testing

Extends the existing `tests/test_static.py` / `tests/features/` pattern
(no Docker required):

- `devcontainer-feature.json` structural checks for `claude-agent` (required fields, `id`
  matches directory, no unexpected `options`), same shape as the existing
  `huggingface`/`transformers` tests.
- `bash -n` syntax check for `install.sh`, `init-firewall.sh`, and `vibe` (extends the
  existing parametrised `test_install_sh_syntax`).
- A check that any `devcontainer.json` referencing the `claude-agent` feature (the kidinnu
  example, plus any future ones) declares both `NET_ADMIN` and `NET_RAW` in `runArgs`.

Actually exercising the firewall — confirming it really blocks non-allowlisted hosts and
allows the allowlisted ones — requires a real container run and is out of scope for the
static test suite. This is a manual verification step to run once after implementation
(build the image, exec in, confirm `curl https://example.com` fails and
`curl https://api.github.com/zen` succeeds), not an automated integration test — adding a
Docker-dependent integration test for this can be a later follow-up if it proves valuable
(following the existing `tests/integration/test_ramalama_sidecar.py` pattern), not part of
this project.
