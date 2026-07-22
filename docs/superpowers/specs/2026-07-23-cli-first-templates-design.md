# CLI-First Templates Design

**Date:** 2026-07-23
**Status:** Approved

## Overview

Make this repo's features easier to consume from a terminal-first devcontainer workflow
(`devpod up` / `devpod ssh`, or the official `@devcontainers/cli`) instead of only via
hand-copied JSON blocks in the README.

**Context on why this looks the way it does:** the original idea was to target
[`squirrelsoft-dev/dev`](https://github.com/squirrelsoft-dev/dev), a Rust CLI with an
appealing layered global-template system (`dev new --template x`). It does not build on
native Windows — `cargo install devcontainer` fails with `E0599` on Unix-only APIs
(`Stdin::as_raw_fd`, `Command::exec`, a raw-fd `libc::read`) in `src/runtime/docker.rs` /
`podman.rs` — and it's early-stage (13 stars, 14 open issues, no release binaries yet).
DevPod is already installed on this machine, is already this repo's documented tool, and
has a full native-Windows CLI (`devpod up`/`ssh`/`stop`/`delete`/`list`). This design targets
DevPod and the official CLI directly; a separate follow-on project (own spec) will build a
small Python CLI that layers `dev`-style named templates on top of `devpod`.

**Non-goal:** replicating `dev`'s layered global-template system in this repo. That's the
follow-on project's job. This repo just ships one complete, runnable `devcontainer.json` per
feature that any devcontainer-spec-compliant CLI can consume.

## Confirmed finding: the sshd wait-loop is dead code

Every existing example (`README.md`, `examples/ollama-sidecar/.devcontainer/devcontainer.json`)
has:

```
"postStartCommand": "until pgrep sshd > /dev/null 2>&1; do sleep 1; done",
```

Nothing in `base/Dockerfile` or any `features/*/install.sh` installs `sshd`. Checked whether
DevPod injects something matching that process name (since `devpod ssh` clearly connects over
SSH somehow) by reading DevPod's source: `cmd/agent/container/ssh_server.go` runs
`devpod agent container ssh-server`, which starts an **embedded Go SSH server**
(`github.com/loft-sh/ssh`, wired up in `pkg/ssh/server/ssh_container.go`) inside the `devpod`
agent process — never a process named `sshd`. The wait-loop has never matched anything,
regardless of which tool is used. Remove it everywhere.

## Changes

### 1. `templates/` directory (new)

One folder per feature in `features/`, each containing a complete, standalone, runnable
`devcontainer.json` — not a fragment requiring further merging:

| Template | Base image | Notes |
|---|---|---|
| `rapids`, `mojo`, `jax`, `pytorch`, `transformers` | `base-cuda` | `runArgs: ["--gpus", "all"]` |
| `marimo` | `base-ubuntu` | README's new CLI section notes swapping to `base-cuda` also works |
| `fastapi`, `cli`, `py-devtools`, `huggingface`, `ollama` | `base-ubuntu` | |
| `agent` | `base-ubuntu` | firewall `runArgs`/`postStartCommand: sudo /usr/local/bin/init-firewall.sh`/`waitFor` per `features/agent/devcontainer-feature.json`'s documented requirement |

Every template:

- `remoteUser: "dev"`
- `postCreateCommand: "pixi install"`
- pixi cache volume mount (`source=<name>-pixi-cache,target=/home/dev/.cache/pixi,type=volume`)
- **no** `postStartCommand: "until pgrep sshd..."` line
- feature reference pinned to `ghcr.io/jesserobertson/devcontainers/<name>:latest`, matching the existing README convention

Usage (documented in the README, not scripted — no installer needed for this sub-project):
copy `templates/<name>/devcontainer.json` into your project's `.devcontainer/devcontainer.json`,
then `devpod up .` (or `npx @devcontainers/cli up --workspace-folder .`), then `devpod ssh <name>`
(or `... exec`).

### 2. README changes

- Remove the `pgrep sshd` line from every existing JSON block.
- Add a short "Using with a CLI (DevPod / devcontainers CLI)" section: copy a template in,
  `devpod up` / `devpod ssh`, one-line mention of the official CLI as a DevPod-free
  alternative.
- Collapse the current per-combo JSON blocks down to exactly one minimal worked example
  (`fastapi`, since it's the simplest complete case) for readers who want to see raw JSON
  inline; every other combo becomes a pointer to `templates/<name>/devcontainer.json` instead
  of a duplicated block.
- `examples/ollama-sidecar/.devcontainer/devcontainer.json`: drop the `pgrep sshd` line, no
  other change (it's a docker-compose sidecar example, structurally different from the
  single-image templates and stays as its own thing).

### 3. Tests

Extend the existing Pester convention (`build-images.tests.ps1`) with a new
`templates.tests.ps1`:

- Every `templates/<name>/devcontainer.json` parses as valid JSON.
- Every template's `features` block references a real `features/<name>/devcontainer-feature.json`
  in this repo.
- No template (or the README, or `examples/`) contains the literal string `pgrep sshd`
  (regression guard now that we've confirmed it should never come back).

## Out of scope

- The Python CLI wrapper around `devpod` (own spec, own brainstorm).
- Any change to DevPod/VS Code Dev Containers extension support — both keep working exactly
  as documented today, this only adds a second, terminal-first path.
