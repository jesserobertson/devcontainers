# base-ubuntu-slim + Homebrew-based cpp-devtools / rust-devtools

**Date:** 2026-08-30
**Status:** Draft

## Overview

Two linked changes:

1. **`base-ubuntu-slim`** — a new pixi-free Ubuntu base image, built from the same
   `base/Dockerfile` as `base-ubuntu` via a new multi-stage layout. It carries the apt
   basics, the `dev` user, Homebrew (installed, no formulae), and the chezmoi dotfiles —
   but none of the Homebrew CLI bundle, no pixi, and no pixi shell-hooks. Login shell is
   bash.
2. **`cpp-devtools` and `rust-devtools` move off pixi and onto Homebrew**, and their
   `templates/*/devcontainer.json` re-base onto `base-ubuntu-slim`. These are the only two
   features that install a native (non-Python) language toolchain, so they are the only
   ones for which pixi was never load-bearing — they used `pixi global install` purely as a
   "put these CLIs on the `dev` user's PATH" mechanism, which Homebrew (already bootstrapped
   in every base image) does equally well.

Nothing else changes. `base-ubuntu`, `base-cuda`, and the other 11 features keep pixi and
keep their current behaviour exactly.

## Motivation

`base/Dockerfile` today installs the pixi *binary* (~25 MB) but never runs
`pixi install` / `pixi global` at build time — features do that at container-create time.
So removing pixi alone is a modest image-size win. The real weight in the base is the
12-formula Homebrew CLI bundle (`neovim` especially), `build-essential`, and the dotfiles.
`base-ubuntu-slim` exists for consumers who want a lean base for a toolchain that installs
via `brew` (or `apt`) and don't need the pixi/Python workflow or the full interactive-shell
kit: `cpp-devtools` and `rust-devtools` are exactly that shape.

## Non-goals

- **No change to `base-ubuntu` or `base-cuda` behaviour.** The `full` stage is a pure
  refactor: same steps, same order, same result. `base-cuda` and every pixi feature are
  unaffected.
- **No `helix` added to the base image.** `helix` is bundled by the features that need it
  (`py-devtools`, `rust-devtools`, `cpp-devtools`); the base stays editor-agnostic apart
  from the `neovim` that is already in the `full` CLI bundle.
- **No separate distroless / `scratch` runtime image.** If a future need arises to ship a
  prebuilt binary in a minimal runtime container, that is its own image with its own spec.
  `core` and `slim` are one published image here — `slim` is `FROM core` with nothing
  added, so a feature that targets the slim image already gets exactly `core`.
- **`dvt` needs no code change.** Its image registry is discovered dynamically from
  `images/*.json`; `DEFAULT_IMAGE` stays `base-ubuntu`.

## `base/Dockerfile` — multi-stage layout

Three stages. `full` is last, so a bare `docker build base/` with no `--target` still
produces today's image (backward-compatible for anyone building the context by hand).

### `core` (`FROM ${BASE_IMAGE}`)

Everything shared by every variant, with **no pixi**:

- apt: `build-essential procps curl file git wget unzip sudo` (unchanged). `build-essential`
  stays — clang needs a working libc, headers, and linker.
- `useradd -m -s /bin/bash dev` (unchanged; no passwordless sudo).
- Homebrew: pre-create `/home/linuxbrew`, `chown dev`, run the installer as `dev`
  (unchanged). **No `brew install` line here.**
- `ENV` for `PATH` (Homebrew `bin`/`sbin` + `/home/dev/.local/bin`), `HOMEBREW_NO_AUTO_UPDATE`,
  `HOMEBREW_NO_ANALYTICS`, `UV_HTTP_TIMEOUT`.
- chezmoi: install binary, `su dev -c 'chezmoi init --apply --no-tty --exclude=scripts
  https://github.com/jesserobertson/dotfiles.git'` (unchanged). Verified safe without the
  CLI bundle: `dot_config/fish/functions/init_cached.fish` and `dot_bashrc.tmpl`'s
  `_init_cached` both `command -v` the tool and `return 0` when absent, so every
  `starship`/`fzf`/`zoxide`/`direnv`/`bat`/`pixi` integration silently no-ops; PATH
  additions are existence-checked (`safe_add_path`) or inert (`fish_add_path` on a missing
  dir). `--exclude=scripts` already means no `run_*` script executes at apply time.
- `HOME=/home/dev git config --global --add safe.directory /workspace`, `chown -R dev:dev
  /home/dev`, `WORKDIR /workspace`, `USER dev`.

Login shell stays **bash** (image default). No `chsh`, no `SHELL` env, no fish, no pixi
shell-hooks in this stage.

### `slim` (`FROM core`)

Marker stage only — `slim` is `core` with nothing added. This is what
`ghcr.io/jesserobertson/base-ubuntu-slim:latest` publishes.

### `full` (`FROM core`)

Adds, in this order (matching today's `base/Dockerfile` tail):

- `USER root`, then `su dev -c 'brew install <BUNDLE>'` where `<BUNDLE>` is the current
  twelve: `bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide`.
- `ENV PIXI_HOME="/home/dev/.local/share/pixi"`, then
  `curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash`.
- `ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish`, `ENV PATH` prepends `$PIXI_HOME/bin`.
- `chsh -s .../fish dev`.
- Write `~/.config/fish/conf.d/project-pixi.fish` and the `~/.bashrc` line that `eval`s
  `pixi shell-hook` when `/workspace/pixi.toml` or `/workspace/pyproject.toml` exists
  (verbatim from today).
- `chown -R dev:dev /home/dev`, `WORKDIR /workspace`, `USER dev`.

`ghcr.io/jesserobertson/base-ubuntu:latest` and the `base-cuda` tags publish from `full`.

### Stage-sharing / cache note

`base-ubuntu` and `base-ubuntu-slim` share the `core` stage. In `build.yml` each matrix job
has its own `cache-from`/`cache-to` scope, so they will not share cached `core` layers
across jobs — a minor rebuild-time inefficiency, accepted rather than entangling the two
jobs' GHA caches.

## Feature conversions

Both features run `install.sh` as root at container-create time; Homebrew refuses to run as
root, so both keep the `su dev -c` pattern (mirrors the base image's own `brew install`).
Both bump their version (content changed since the published `1.0.0`):

### `rust-devtools` → `1.1.0`

```bash
#!/bin/bash
set -e

su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    rust rust-analyzer helix'
```

`rust`, `rust-analyzer`, `helix` are all normal (non-keg-only) formulae, symlinked into
`$(brew --prefix)/bin`, which is already on the `dev` user's PATH. Helix's built-in default
config already pairs Rust with `rust-analyzer`, so no `languages.toml` wiring — same as
today.

### `cpp-devtools` → `1.1.0`

```bash
#!/bin/bash
set -e

su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    llvm cmake ninja ccache pkgconf helix'

# Homebrew's llvm is keg-only: its clang/clang++/clangd/lld/lldb/clang-format/
# clang-tidy live in opt/llvm/bin and are NOT symlinked onto PATH. Add that
# directory for the dev user so `clang` and `clangd` (Helix's default C/C++
# language server) resolve. `make` comes from the base image's build-essential.
# Written as root then chowned - same pattern as py-devtools' languages.toml.
LLVM_BIN=/home/linuxbrew/.linuxbrew/opt/llvm/bin
mkdir -p /home/dev/.config/fish/conf.d
echo "fish_add_path -gp $LLVM_BIN" > /home/dev/.config/fish/conf.d/cpp-devtools.fish
echo "export PATH=\"$LLVM_BIN:\$PATH\"" >> /home/dev/.bashrc
chown dev:dev /home/dev/.config/fish/conf.d/cpp-devtools.fish
```

- `cmake`, `ninja`, `ccache`, `pkgconf`, `helix` are normal formulae, already on PATH.
- Debugger is `lldb` (ships in `llvm`); no `gdb`.
- The two-line PATH snippet is the C/C++ analogue of `py-devtools` writing
  `languages.toml`: a small amount of image-time wiring so the editor works out of the box.
- On the slim base, dotfiles set `EDITOR=hx` — satisfied here since this feature installs
  `helix`.

### Feature JSON

`description` strings updated to say "via Homebrew" and to list the real payload. `options`
stay `{}`. `id`/`name` unchanged.

## `images/base-ubuntu-slim.json`

```json
{
  "name": "base-ubuntu-slim",
  "description": "Ubuntu 24.04 devcontainer base with Homebrew and the chezmoi dotfiles but no pixi and no CLI bundle - a lean base for toolchains that install via brew or apt.",
  "ref": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
  "aliases": ["ubuntu-slim", "slim"]
}
```

Picked up automatically by `dvt sync` (name matches `^[a-z0-9][a-z0-9-]*$`).

## Build plumbing

### `.github/workflows/build.yml`

- Add `target:` to every matrix entry: `full` for `base-ubuntu` and `base-cuda`, `slim` for
  the new `base-ubuntu-slim`.
- Add `target: ${{ matrix.target }}` to the `docker/build-push-action` step.
- New matrix row:
  ```yaml
  - name: base-ubuntu-slim
    base_image: ubuntu:24.04
    target: slim
    tags: ghcr.io/jesserobertson/base-ubuntu-slim:latest
  ```
- The `paths:` trigger (`base/Dockerfile`, `.github/workflows/build.yml`) already covers
  this change.

### `build-images.ps1`

- Each `$ImageDefs` entry gains an optional `Target` key (`full` / `slim`; `ramalama` has
  none).
- In the local-build branch, append `--target $def.Target` when set. Pull mode unaffected.
- Add `base-ubuntu-slim` to the `-Images` `[ValidateSet(...)]` and to the default `$Images`
  array.

### `build-images.tests.ps1`

- New assertions: `base-ubuntu-slim` build invokes `docker` with `--target slim`;
  `base-ubuntu` build invokes it with `--target full`.
- Existing tests (context, tags, build-arg) are unaffected.

## `templates/{cpp,rust}-devtools/devcontainer.json`

```json
{
  "name": "cpp-devtools",
  "description": "C/C++ toolchain (clang, cmake, ninja) plus clangd, lldb and Helix, via Homebrew on the slim base.",
  "image": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/cpp-devtools:latest": {}
  },
  "remoteUser": "dev"
}
```

Dropped vs the pixi templates: `postCreateCommand` (no pixi; the toolchain is installed by
the feature at create time) and the `pixi-cache` volume mount. `rust-devtools` template is
the same shape.

## `tests/test_static.py`

- `IMAGES` += `"base-ubuntu-slim"`; add a ref assertion
  (`== ghcr.io/jesserobertson/base-ubuntu-slim:latest`).
- New `SLIM_TEMPLATE_FEATURES = ["rust-devtools", "cpp-devtools"]` with parametrized checks:
  template `image` is the slim ref, references its own feature, `remoteUser == "dev"`, no
  `pgrep sshd` wait-loop.
- Remove `"rust-devtools"` and `"cpp-devtools"` from `CPU_TEMPLATE_FEATURES` (they no longer
  use `base-ubuntu`).
- Remove both from `SU_DEV_FEATURES`. Add `BREW_FEATURES = ["rust-devtools",
  "cpp-devtools"]` with `test_brew_calls_run_as_dev`: every line containing `brew install`
  also contains `su dev -c` (Homebrew refuses root).
- `test_template_post_create_enables_detached_environments` parametrization changes from
  `FEATURES` to `[f for f in FEATURES if f not in SLIM_TEMPLATE_FEATURES]` — the slim
  templates deliberately have no `postCreateCommand`.
- `FEATURES` itself keeps both (their `devcontainer-feature.json` still validates); the
  GHCR drift check (`test_published_feature_version_matches_local_content`) handles the
  version bump on its own — a bumped-but-unpublished version returns `None` and passes.

## Docs

- **README** — new row in the Base images table for `base-ubuntu-slim`; update the
  `cpp-devtools` / `rust-devtools` feature-table rows to say "via Homebrew".
- **CHANGELOG** — under `## 2026-08-30`: `base-ubuntu-slim` added; `cpp-devtools` /
  `rust-devtools` moved to Homebrew and re-based on the slim image.
- **RELEASE.md** — the "Base images (`base/Dockerfile`)" section (line 79) gains a sentence
  noting the three build targets and that `base-ubuntu-slim` publishes from `--target slim`.

## Risks

- **Upstream dotfiles drift.** The safety analysis above depends on `init_cached` /
  `_init_cached` staying defensive. If a future dotfiles change adds an unguarded tool call
  to a fish `conf.d/` file or `dot_bashrc.tmpl`, slim shells could print `command not
  found`. Mitigation if it happens: an extra `chezmoi ... --exclude` path or a guard
  upstream. Not blocking.
- **`llvm` keg-only PATH snippet.** If Homebrew ever links `llvm` by default, the snippet
  becomes redundant (harmless). If the `opt/llvm` path changes, `cpp-devtools` breaks until
  updated — low likelihood, and caught by anyone actually using the feature.
- **`build-images.ps1` / `build.yml` divergence.** Two build entry points now both need the
  `target` concept. The new `build-images.tests.ps1` assertions guard the PowerShell side;
  `build.yml` is covered only by CI actually building. Accepted.
