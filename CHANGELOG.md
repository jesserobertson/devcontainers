# Changelog

Notable changes to the base images, devcontainer features, and templates in this repo.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), with dated
sections instead of version numbers - this repo doesn't ship as a single versioned artifact,
each feature and base image is versioned (and published to `ghcr.io/jesserobertson`)
independently.

For changes to the `dvt` CLI itself, see [`dvt/CHANGELOG.md`](dvt/CHANGELOG.md) instead.

## 2026-09-03

### Added

- `homebrew`, `shell-kit` and `pixi` features (all `1.0.0`) - the Homebrew
  package-manager layer, the interactive fish + CLI bundle, and pixi with its
  bash/fish project shell-hooks, each extracted from `base/Dockerfile`'s `full`
  stage into a standalone published feature.
- `base-cuda-slim` base image - the CUDA 12.8 counterpart of `base-ubuntu-slim`
  (apt kit + chezmoi dotfiles, no Homebrew, no pixi, no CLI bundle).

### Changed

- `base/Dockerfile` is now `core` -> `slim` only; its `full` stage is dissolved
  into the new `homebrew`, `shell-kit` and `pixi` features. The two `-slim`
  images are the only direct `docker build` output; `base-ubuntu` and `base-cuda`
  are now assembled from `<matching -slim> + homebrew + shell-kit + pixi` via
  `devcontainer build` (`build.yml` split into `build-slim` + `build-bundles`).
- Python toolchain templates (`cli`, `fastapi`, `marimo`, `huggingface`,
  `ollama`, `py-devtools`) re-pointed to `base-ubuntu-slim`; GPU templates
  (`jax`, `pytorch`, `rapids`, `transformers`, `mojo`) to `base-cuda-slim`.
  `agent` and `podman` stay on the batteries-included `base-ubuntu`.
- All 11 Python toolchain features gain `dependsOn: pixi` so they self-provision
  pixi on a slim base (`cli`, `fastapi`, `huggingface`, `jax`, `marimo`, `mojo`,
  `pytorch`, `rapids`, `transformers` -> `1.2.0`; `ollama` -> `1.1.0`;
  `py-devtools` -> `1.3.0`).
- `rust-devtools` and `cpp-devtools` gain `dependsOn: homebrew` (both -> `1.2.0`)
  - **breaking** for anyone layering their own `brew install` directly on
  `base-ubuntu-slim` without also listing the `homebrew` feature: `-slim` no
  longer ships Homebrew, so `brew` is `command not found` until that feature
  installs it.

## 2026-08-30

### Added

- `base-ubuntu-slim` base image - Ubuntu 24.04 with Homebrew and the chezmoi dotfiles but
  no pixi and no Homebrew CLI bundle, built from a new `core` stage in `base/Dockerfile`.
  `base-ubuntu` and `base-cuda` are unchanged (they build the `full` stage).
- `cpp-devtools` feature - C/C++ toolchain (clang/clang++, clangd, lld, lldb, cmake, ninja,
  ccache, pkgconf) plus the Helix editor, installed via Homebrew. Adds Homebrew's keg-only
  `llvm` bin dir to PATH so `clang`/`clangd` resolve; Helix's default config already pairs
  C/C++ with clangd.

### Changed

- `rust-devtools` and `cpp-devtools` now install via Homebrew instead of `pixi global
  install`, and their templates target the new `base-ubuntu-slim` image. Both bumped to
  `1.1.0`. The features still work on `base-ubuntu` / `base-cuda` (Homebrew is present in
  all three).

## 2026-08-16

### Added

- `rust-devtools` feature - Rust toolchain (cargo, rustc) plus rust-analyzer and the Helix
  editor, installed via pixi. Helix's own default language config already pairs Rust with
  rust-analyzer, so unlike `py-devtools` it needs no extra `languages.toml` wiring.
- `podman` feature - rootless Podman for nested container/image testing, with `docker`/
  `docker compose` CLI shims (podman-docker, podman-compose) so existing tooling that shells
  out to `docker` keeps working unchanged. Uses the `vfs` storage driver so it runs inside any
  unprivileged container with no `--privileged`/`--device=/dev/fuse` needed on a normal
  (rootful) host - documented as not working for nested runs on an already-rootless outer
  engine (e.g. Podman Machine on Windows), since no subordinate UID range is left to delegate
  further.
- `py-devtools` now also installs the Helix editor and pyright, and writes
  `~/.config/helix/languages.toml` wiring Python to ruff's native LSP (lint/format) and pyright
  (hover/completion/go-to-definition) out of the box.

### Changed

- This repo's own `.devcontainer` now dogfoods `dvt` (`py-devtools` + `podman`) instead of
  having no devcontainer of its own.

## 2026-08-15

### Fixed

- Every feature's `install.sh` had drifted from what was actually published on GHCR for
  months, because `devcontainer-feature.json`'s `version` field was never bumped alongside
  content changes - `devcontainers/action`'s publish step is version-keyed, so pushing an
  unchanged version against an already-published artifact was a silent no-op. Bumped every
  feature's version to force a republish, and added a test that fetches the published artifact
  and fails if local content has changed without a version bump, to catch this recurring.
- Every feature template was missing `pixi`'s `detached-environments` setting, which broke
  `pixi install` outright on Windows hosts (`.pixi/envs` written straight onto the bind-mounted
  workspace fails with "Operation not permitted" there).

## 2026-08-10 – 2026-08-11

### Added

- A `description` field on every feature template's `devcontainer.json`, surfaced by `dvt
  feature list`/`show`/`info`.

### Fixed

- Every feature template now shares one `pixi-cache` named volume instead of a separate volume
  per template.

## 2026-07-23

### Added

- `templates/` - a ready-to-copy `devcontainer.json` per feature (every GPU/CPU combo, plus
  `agent`), for driving a workspace without VS Code via DevPod or `@devcontainers/cli`.

### Changed

- README: per-combination JSON blocks replaced with pointers into `templates/` plus a
  CLI-usage section.

### Fixed

- Dead `sshd` wait-loop removed from the `ollama-sidecar` example, with a regression test
  guarding against it coming back.

## 2026-07-16 – 2026-07-17

### Added

- `agent` feature (shipped initially as `claude-agent`, renamed same week): Claude Code, Pi,
  and oh-my-pi CLIs, usable against the same Anthropic account or a local model, plus an
  egress-allowlist firewall and a `vibe` wrapper for opt-in unattended auto mode.

### Changed

- Replaced the `ramalama` local-LLM setup with real Ollama; moved `host-services/ollama` off
  port 11434 to avoid colliding with a native Ollama install on the host.

### Fixed

- `PIXI_HOME` set explicitly so pixi never creates a bare `~/.pixi` dir; firewall/iptables
  fixes (policies reset to ACCEPT before flush, quieter per-IP logging); pi/omp config dirs
  pre-created and dev-owned so they survive being mounted as volumes.

## 2026-07-13 – 2026-07-15

### Changed

- Base image migrated to a dedicated non-root `dev` user; passwordless sudo dropped entirely
  (the `agent` feature's firewall script is the one narrowly-scoped exception, added later).
  Every feature's pixi install now runs as `dev`, not root.

### Added

- `build-images.ps1` - local helper script (with Pester tests) for building/publishing base
  images and packaging features, matching what CI does.

### Fixed

- Homebrew install steps run as the non-root user; pixi/pip calls in feature install scripts
  use fully-qualified paths so `su dev -c` invocations (which reset `PATH`) actually find them.

## 2026-05-04 – 2026-05-06

### Added

- `huggingface` and `transformers` features; a `ramalama` client feature plus host service and
  image for local LLM hosting.
- The pytest suite (static structural validation, per-feature unit tests, integration tests)
  that still gates every feature/template today.
- `examples/ramalama-sidecar` - a compose-based sidecar devcontainer example.

## 2026-05-01

### Added

- Initial release: `base-ubuntu`/`base-cuda` images. Started the same day as four pre-built ML
  "flavour" images (rapids/mojo/jax/pytorch); replaced by end of day with the composable
  devcontainer-features model still used today, where each feature ships its own default
  `pixi.toml` and runs `pixi install` at container-create time. Also added the `marimo`, `cli`,
  `fastapi`, and `py-devtools` features.

### Fixed

- `rapids` feature stripped down to cuDF + Polars GPU only (from a broader initial package set).
