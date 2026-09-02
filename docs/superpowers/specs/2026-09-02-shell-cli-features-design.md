# Shell & CLI tooling as composable features (homebrew / shell-kit / pixi)

**Date:** 2026-09-02
**Status:** Draft

## Overview

Pull the shell and CLI plumbing out of `base/Dockerfile`'s `full` stage into three
independently published devcontainer features, and assemble the "batteries-included" base
images from them:

1. **`features/homebrew`** — installs Homebrew itself (the package manager, no formulae).
2. **`features/shell-kit`** — the interactive CLI bundle (`bat eza fd fish fzf jq just
   neovim ripgrep starship zoxide` + `bat-extras`) and the fish login shell. `dependsOn`
   `homebrew`.
3. **`features/pixi`** — the pixi binary, its env, and the project pixi `shell-hook`
   snippets for bash and fish. `installsAfter` `homebrew` / `shell-kit`.

`base/Dockerfile` loses its `full` stage. It now publishes only the bare-core images
`base-ubuntu-slim` and (new) `base-cuda-slim`. The bundle images `base-ubuntu` /
`base-cuda` are rebuilt via `devcontainer build` as `<the matching -slim image> + homebrew
+ shell-kit + pixi`, so the bundle genuinely *is* slim plus the three features — one code
path, no drift.

Downstream:

- Every pixi-based feature (`cli`, `fastapi`, `huggingface`, `jax`, `marimo`, `mojo`,
  `ollama`, `py-devtools`, `pytorch`, `rapids`, `transformers`) gains `dependsOn: pixi`.
- `rust-devtools` / `cpp-devtools` gain `dependsOn: homebrew` — now **required**, because
  `slim` no longer ships Homebrew.
- Python toolchain templates re-point from `base-ubuntu` → `base-ubuntu-slim`; GPU
  toolchain templates from `base-cuda` → `base-cuda-slim`. `agent` and `podman` templates
  stay on `base-ubuntu`.

`dvt` gains dependency awareness: `dvt sync` also fetches every feature's
`devcontainer-feature.json`, a new resolver walks the `dependsOn` graph, and four UI
surfaces expose it (`feature list` column, `feature show` tree, new `feature deps`
command, `feature add` message).

## Motivation

`base/Dockerfile`'s `full` stage is a fixed bundle: you get Homebrew + the 11-formula CLI
kit + fish + pixi + pixi hooks, all or nothing. Consumers who want just pixi on a lean
base, or just Homebrew for a native toolchain, currently can't express that — `slim` gives
too little (added by the 2026-08-30 change for exactly `rust-devtools` / `cpp-devtools`)
and `full` gives everything.

Making each piece a feature lets:

- The base images be *assembled* rather than hand-maintained — `base-ubuntu` can't drift
  from "slim + the features" because that's literally how it's built.
- Toolchain features declare what they need (`dependsOn: pixi`) instead of assuming a
  `full` base, so they compose onto `slim` directly.
- `dvt` show the user what a feature pulls in ("add `rapids`, get `pixi` too").

The 2026-08-30 spec (`base-ubuntu-slim`) is the immediate predecessor: it introduced the
`core` / `slim` / `full` split and moved the two native-toolchain features to Homebrew.
This change finishes that trajectory by dissolving `full` into features.

## Non-goals

- **No behavioural change to `base-ubuntu` / `base-cuda` for existing consumers.** The
  bundle images stay published at the same refs and come out functionally equivalent to
  today's `full` (fish login shell, CLI kit, pixi + hooks). Layer structure and image size
  differ — feature installs are their own layers with no apt cache-mount sharing.
- **No auto-injection of implied features into `devcontainer.json`.** `dvt feature add`
  *reports* what a feature pulls in and records it in the sidecar; it does not write the
  implied features into the project's `devcontainer.json`. The devcontainer spec already
  applies `dependsOn` at build time. Injecting them would duplicate that and add
  merge/remove/cleanup complexity (see "dvt — explicitly out of scope").
- **No new `dvt` command for *building* base images.** `dvt` consumes finished images from
  GHCR; assembling them is `build.yml` / `build-images.ps1` only.
- **No parallelised `dvt sync`.** Feature-spec fetches roughly double the request count;
  the existing `on_err` retry wrapper covers it. Parallelising is a later optimisation.
- **No change to how features run** — `install.sh` still runs as root at
  container-create / image-build time, still uses `su dev -c` for brew/pixi.

## Feature specifications

All three live under `features/`, are packaged by the existing `publish-features.yml`
(`devcontainers/action`, `base-path-to-features: features`), and publish to
`ghcr.io/jesserobertson/devcontainers/<id>` — the same namespace every existing feature
and template ref uses. Each is released with a `feat-<id>-v<version>` tag. All start at
`1.0.0`.

Feature-option env-var mapping (devcontainer spec): option `loginShell` → `$LOGINSHELL`,
`shellHook` → `$SHELLHOOK`, `global` → `$GLOBAL`.

### `features/homebrew`

**`devcontainer-feature.json`**

```json
{
  "id": "homebrew",
  "version": "1.0.0",
  "name": "Homebrew (Linuxbrew)",
  "description": "Installs Homebrew for the non-root 'dev' user at the standard /home/linuxbrew/.linuxbrew prefix, with no formulae. Puts brew on PATH and disables auto-update and analytics. This is the package-manager layer that shell-kit, rust-devtools and cpp-devtools build on; base-ubuntu and base-cuda bundle it. Idempotent - a no-op if brew is already present.",
  "options": {},
  "containerEnv": {
    "PATH": "/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/dev/.local/bin:${PATH}",
    "HOMEBREW_NO_AUTO_UPDATE": "1",
    "HOMEBREW_NO_ANALYTICS": "1"
  },
  "installsAfter": ["ghcr.io/devcontainers/features/common-utils"]
}
```

**`install.sh`**

```bash
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
```

Lifted verbatim from today's `core` stage (the `core` stage keeps it too until this
change; see "base/Dockerfile"). The `common-utils` `installsAfter` is defensive
convention — we don't use that feature, but it marks homebrew as an early-running layer if
a consumer ever combines them.

### `features/shell-kit`

**`devcontainer-feature.json`**

```json
{
  "id": "shell-kit",
  "version": "1.0.0",
  "name": "Interactive shell kit (fish + modern CLI tools)",
  "description": "Installs a modern interactive CLI bundle via Homebrew - bat, bat-extras, eza, fd, fish, fzf, jq, just, neovim, ripgrep, starship, zoxide - and makes fish the 'dev' user's login shell (set loginShell=false to keep bash). The chezmoi dotfiles already wire starship/fzf/zoxide integrations when these tools are present. Pulls in the homebrew feature.",
  "options": {
    "loginShell": {
      "type": "boolean",
      "default": true,
      "description": "chsh the dev user to fish. When false, installs the tools but leaves the login shell as bash."
    }
  },
  "containerEnv": {
    "SHELL": "/home/linuxbrew/.linuxbrew/bin/fish"
  },
  "dependsOn": {
    "ghcr.io/jesserobertson/devcontainers/homebrew": {}
  },
  "installsAfter": ["ghcr.io/jesserobertson/devcontainers/homebrew"]
}
```

`dependsOn` is what makes "add `shell-kit`" (or any feature that depends on it) pull
`homebrew` in automatically. `installsAfter` is also listed so ordering holds even in
resolvers/toolchains that honour `installsAfter` but not `dependsOn`.

Note `SHELL` is set unconditionally in `containerEnv` even though `loginShell=false` skips
the `chsh`. This matches today's `full` behaviour (which always exports `SHELL=…/fish`);
when `loginShell=false` the user gets bash as the actual login shell but `$SHELL` still
points at fish, same as if they'd installed fish by hand without `chsh`. Acceptable; a
`loginShell=false` consumer that cares can override `SHELL` themselves.

**`install.sh`**

```bash
#!/bin/bash
set -e

su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

if [ "${LOGINSHELL:-true}" = "true" ]; then
    chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev
fi
```

The bundle is the exact 11 formulae (+`bat-extras`) from today's `full` stage. This
feature does **not** own the pixi shell-hook fish snippet — that moves to `pixi`, which
writes it only if fish exists, so `pixi` degrades on a slim base without `shell-kit`.

### `features/pixi`

**`devcontainer-feature.json`**

```json
{
  "id": "pixi",
  "version": "1.0.0",
  "name": "pixi",
  "description": "Installs the pixi package manager for the 'dev' user with PIXI_HOME at ~/.local/share/pixi, puts it on PATH, and (shellHook=auto) writes bash + fish shell-hook snippets that activate a /workspace pixi environment on shell open when pixi.toml or pyproject.toml is present. Optionally runs 'pixi global install' for a given package list. This is the layer every Python toolchain feature (py-devtools, fastapi, cli, ...) depends on.",
  "options": {
    "shellHook": {
      "type": "string",
      "enum": ["auto", "off"],
      "default": "auto",
      "description": "auto: write the bash + fish project pixi shell-hook snippets (fish only if fish is installed). off: install pixi only."
    },
    "global": {
      "type": "string",
      "default": "",
      "description": "Optional space-separated package list to 'pixi global install --environment dev --channel conda-forge' at install time."
    }
  },
  "containerEnv": {
    "PIXI_HOME": "/home/dev/.local/share/pixi",
    "PATH": "/home/dev/.local/share/pixi/bin:${PATH}",
    "UV_HTTP_TIMEOUT": "300"
  },
  "installsAfter": [
    "ghcr.io/jesserobertson/devcontainers/homebrew",
    "ghcr.io/jesserobertson/devcontainers/shell-kit"
  ]
}
```

`installsAfter` (not `dependsOn`) homebrew/shell-kit: `pixi` works fine without either, but
when all three are present the order must be homebrew → shell-kit → pixi (so the fish hook
snippet lands after fish exists), matching today's `full`.

**`install.sh`**

```bash
#!/bin/bash
set -e

# The dotfiles repo redirects PIXI_HOME to ~/.local/share/pixi via a plain
# config file, but Docker/feature RUN steps don't source shell profiles and
# chezmoi apply already ran in the base image. Set it explicitly so the
# installer places the binary where every other pixi invocation (every
# feature's install.sh, the shell hooks) looks for it.
export PIXI_HOME="/home/dev/.local/share/pixi"
curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash

if [ "${SHELLHOOK:-auto}" = "auto" ]; then
    # bash: always. fish: only if fish is installed (shell-kit present).
    su dev -c 'echo "if [ -f /workspace/pixi.toml ] || [ -f /workspace/pyproject.toml ]; then eval \"\$(pixi shell-hook --manifest-path /workspace --shell bash)\"; fi" >> /home/dev/.bashrc'
    if [ -x /home/linuxbrew/.linuxbrew/bin/fish ]; then
        su dev -c 'mkdir -p /home/dev/.config/fish/conf.d && printf "if status is-interactive\n    if test -f /workspace/pixi.toml; or test -f /workspace/pyproject.toml\n        eval (pixi shell-hook --manifest-path /workspace --shell fish)\n    end\nend\n" > /home/dev/.config/fish/conf.d/project-pixi.fish'
    fi
fi

if [ -n "${GLOBAL:-}" ]; then
    su dev -c "/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge ${GLOBAL}"
fi
```

The `.bashrc` line and `conf.d/project-pixi.fish` content are verbatim from today's `full`
stage. `global` is a convenience so a one-off consumer can do `"pixi": { "global": "ruff
mypy" }` without authoring a feature; the real toolchain features keep their own
`install.sh` and just `dependsOn: pixi`.

### Dependency edges

```
shell-kit ──dependsOn──▶ homebrew
pixi ──installsAfter──▶ homebrew, shell-kit
cli, fastapi, huggingface, jax, marimo, mojo,
  ollama, py-devtools, pytorch, rapids, transformers ──dependsOn──▶ pixi
rust-devtools, cpp-devtools ──dependsOn──▶ homebrew
agent, podman ── (no new edge)
```

## `base/Dockerfile`

`full` stage **removed**. The `core` → `slim` structure from the 2026-08-30 spec stays.
One change to `core`: the Homebrew block (`mkdir -p /home/linuxbrew`, `chown`, the
`NONINTERACTIVE=1` installer run) is **removed** from `core` and now lives only in
`features/homebrew`. Everything else in `core` is unchanged:

- apt: `build-essential procps curl file git wget unzip sudo`.
- `useradd -m -s /bin/bash dev`, no passwordless sudo.
- `ENV` for `HOMEBREW_NO_AUTO_UPDATE` / `HOMEBREW_NO_ANALYTICS` / `UV_HTTP_TIMEOUT` — keep
  these in `core` (harmless when brew/pixi absent; `homebrew`/`pixi` features re-declare
  the ones they own via `containerEnv`, which is idempotent). `PATH` keeps
  `/home/dev/.local/bin`; the linuxbrew and pixi `bin` dirs move to the features'
  `containerEnv`.
- chezmoi: install binary + `su dev -c 'chezmoi init --apply --no-tty --exclude=scripts
  …'`. Unchanged — the dotfiles degrade gracefully when brew/pixi/CLI tools are absent
  (verified in the 2026-08-30 spec: `init_cached` / `_init_cached` `command -v` every tool
  and no-op when missing).
- `git config --global --add safe.directory /workspace`, `chown -R dev:dev /home/dev`,
  `WORKDIR /workspace`, `USER dev`.

Login shell stays bash. `base/Dockerfile` ends at ~25 lines.

After this change `slim` is `FROM core` with nothing added (marker stage), same as
2026-08-30.

## Bundle image assembly (`devcontainer build`)

### New build-config directories

```
images/base-ubuntu/.devcontainer/devcontainer.json
images/base-cuda/.devcontainer/devcontainer.json
```

`images/base-ubuntu/.devcontainer/devcontainer.json`:

```json
{
  "name": "base-ubuntu",
  "image": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/homebrew:latest": {},
    "ghcr.io/jesserobertson/devcontainers/shell-kit:latest": {},
    "ghcr.io/jesserobertson/devcontainers/pixi:latest": {}
  }
}
```

`images/base-cuda/.devcontainer/devcontainer.json` is identical except
`"image": "ghcr.io/jesserobertson/base-cuda-slim:latest"`. `dependsOn` / `installsAfter`
in the feature specs fix the apply order, so the listing order here is irrelevant.

These sit **beside** the existing `images/*.json` registry-metadata files (which `dvt`
reads); the `.devcontainer/` subdir keeps the `devcontainer build` config from colliding
with `base-ubuntu.json`.

### `images/` registry metadata

- Keep `base-ubuntu.json`, `base-ubuntu-slim.json`, `base-cuda.json`.
- **Add `images/base-cuda-slim.json`:**
  ```json
  {
    "name": "base-cuda-slim",
    "description": "Bare CUDA 12.8 devcontainer core - apt kit, the dev user, and the chezmoi dotfiles, with no Homebrew, no pixi and no CLI bundle. The lean base GPU toolchain features compose onto.",
    "ref": "ghcr.io/jesserobertson/base-cuda-slim:latest",
    "aliases": ["cuda-slim", "gpu-slim"]
  }
  ```
- Update `base-ubuntu.json` / `base-cuda.json` descriptions to "= <the -slim image> +
  homebrew + shell-kit + pixi features".

### Image lineage

| Published image | Built by | Contents |
|---|---|---|
| `base-ubuntu-slim` | `docker build --target slim`, `BASE_IMAGE=ubuntu:24.04` | apt kit, `dev` user, chezmoi dotfiles |
| `base-cuda-slim` | `docker build --target slim`, `BASE_IMAGE=nvidia/cuda:12.8.0-devel-ubuntu24.04` | same, on CUDA |
| `base-ubuntu` | `devcontainer build` on `base-ubuntu-slim` | + homebrew + shell-kit + pixi |
| `base-cuda` | `devcontainer build` on `base-cuda-slim` | + homebrew + shell-kit + pixi |

## Build plumbing

### `.github/workflows/build.yml`

Split the single matrix into two jobs.

**`build-slim`** — unchanged mechanics, reduced matrix:

```yaml
strategy:
  matrix:
    include:
      - name: base-ubuntu-slim
        base_image: ubuntu:24.04
        tags: ghcr.io/jesserobertson/base-ubuntu-slim:latest
      - name: base-cuda-slim
        base_image: nvidia/cuda:12.8.0-devel-ubuntu24.04
        tags: ghcr.io/jesserobertson/base-cuda-slim:latest
```

`docker/build-push-action` with `context: base`, `target: slim`, per-name GHA cache. The
old `base-ubuntu` / `base-cuda` / `target: full` rows are gone.

**`build-bundles`** — `needs: build-slim`:

```yaml
steps:
  - uses: actions/checkout@v7
  - uses: docker/login-action@v4      # GHCR, same as build-slim
  - run: npm install -g @devcontainers/cli
  - run: |
      devcontainer build \
        --workspace-folder images/${{ matrix.name }} \
        --push true \
        --image-name ${{ matrix.primary_tag }} \
        ${{ matrix.extra_tag && format('--image-name {0}', matrix.extra_tag) || '' }}
```

matrix: `base-ubuntu` (`primary_tag: ghcr.io/jesserobertson/base-ubuntu:latest`),
`base-cuda` (`primary_tag: …/base-cuda:latest`, `extra_tag: …/base-cuda:cuda12.8.0`).

**Trigger `paths:`** widen to:

```yaml
- base/Dockerfile
- images/**
- features/homebrew/**
- features/shell-kit/**
- features/pixi/**
- .github/workflows/build.yml
```

**Feature-publish lag.** `build-bundles` pulls the *published* `…/homebrew:latest` etc.
from GHCR. `publish-features.yml` (triggered on `features/**`) and `build.yml` run as
independent workflows on the same push, with no ordering guarantee. If a push changes a
plumbing feature *and* something under `base/` or `images/`, the bundle build may grab the
previous feature version. Consequences are limited (`:latest` converges on the next
`build.yml` run — a `workflow_dispatch` or any later push). Accepted for now; a
`workflow_run:` trigger making `build-bundles` wait for `publish-features` is the fix if
this bites. Documented as a known lag.

### `build-images.ps1`

- `$ImageDefs`: `base-ubuntu-slim` / `base-cuda-slim` keep the docker shape (`Context =
  'base'`, `Target = 'slim'`, `BuildArgs`). `base-ubuntu` / `base-cuda` change to
  `Builder = 'devcontainer'`, `ConfigDir = 'images/base-ubuntu'` (resp. `images/base-cuda`),
  `Tags` as today.
- New `Invoke-DevcontainerBuild` helper: `devcontainer build --workspace-folder <ConfigDir>
  --image-name <tag>` per tag (local build → omit `--push`; the function loads the result
  into the local docker image store, which `devcontainer build` does by default).
- The build loop dispatches on `$def.Builder` (`'devcontainer'` vs default docker).
- `-Pull` branch unchanged — pulls finished images from GHCR regardless of how they were
  built.
- `-Images` `[ValidateSet(...)]` and default `$Images`: add `base-cuda-slim`. Add a
  dependency nudge mirroring the ramalama→base-cuda one: selecting `base-ubuntu` without
  `base-ubuntu-slim` prepends it (and `base-cuda` → `base-cuda-slim`), with a warning.
- `-Features` `[ValidateSet(...)]` and default list: add `homebrew`, `pixi`, `shell-kit`.
- Guard: if `$def.Builder -eq 'devcontainer'` and `devcontainer` CLI is absent, throw the
  same "install it with npm i -g @devcontainers/cli" message the feature-package path
  already uses.

### `build-images.tests.ps1`

- `base-ubuntu-slim` / `base-cuda-slim` invoke `docker build … --target slim`.
- `base-ubuntu` / `base-cuda` invoke `devcontainer build --workspace-folder
  images/<name> --image-name <tag>` (once per tag for `base-cuda`).
- The `base-ubuntu → base-ubuntu-slim` (and cuda) auto-add nudge fires and warns.
- Feature default list includes `homebrew` / `pixi` / `shell-kit`.
- Existing context/tags/build-arg assertions for the slim images unaffected.

## Downstream features

Each edited feature gets **only** a `dependsOn` block added to its
`devcontainer-feature.json` plus a minor version bump (content-relevant metadata changed)
and a `feat-<id>-v*` tag. No `install.sh` changes. The version numbers below assume the
currently published versions; the implementer reads each feature's current `version` and
bumps the minor component.

| Feature | Add to `devcontainer-feature.json` | New version |
|---|---|---|
| `cli` | `"dependsOn": { "ghcr.io/jesserobertson/devcontainers/pixi": {} }` | `1.2.0` |
| `fastapi` | same | minor bump |
| `huggingface` | same | minor bump |
| `jax` | same | minor bump |
| `marimo` | same | minor bump |
| `mojo` | same | minor bump |
| `ollama` | same | minor bump |
| `py-devtools` | same | `1.3.0` |
| `pytorch` | same | minor bump |
| `rapids` | same | minor bump |
| `transformers` | same | minor bump |
| `rust-devtools` | `"dependsOn": { "ghcr.io/jesserobertson/devcontainers/homebrew": {} }` | `1.2.0` |
| `cpp-devtools` | same (homebrew) | `1.2.0` |
| `agent` | — | — |
| `podman` | — | — |

`ramalama` (referenced in `build-images.ps1` but with no `templates/` entry) gets
`dependsOn: pixi` too if it has a `features/ramalama/devcontainer-feature.json` with a
`pixi global` install; confirm during implementation.

The `rust-devtools` / `cpp-devtools` edge is load-bearing: after this change `slim` has no
Homebrew, so without `dependsOn: homebrew` a fresh `rust-devtools` container on
`base-ubuntu-slim` fails at `brew: command not found`.

## Templates

| Template group | `image` was | `image` becomes |
|---|---|---|
| `cli`, `fastapi`, `marimo`, `huggingface`, `ollama`, `py-devtools` | `…/base-ubuntu:latest` | `…/base-ubuntu-slim:latest` |
| `jax`, `pytorch`, `rapids`, `transformers`, `mojo` | `…/base-cuda:latest` | `…/base-cuda-slim:latest` |
| `rust-devtools`, `cpp-devtools` | `…/base-ubuntu-slim:latest` | unchanged |
| `agent`, `podman` | `…/base-ubuntu:latest` | unchanged |

Only the `image` field changes for the re-pointed templates. `features`, `mounts`,
`postCreateCommand` (`pixi install` etc.), `remoteUser` all stay — pixi arrives via the
feature's `dependsOn`, so `postCreateCommand: pixi install` still resolves.

### Tradeoff: interactive shell on re-pointed templates

A `py-devtools` (etc.) project now lands on `base-ubuntu-slim`: bash login shell, no
`fish` / `starship` / `bat` / `eza` / `fd` / `fzf` / `ripgrep` / `zoxide`. The pixi
workflow, `ruff`/`mypy`/`pytest`/`helix`, and the pixi shell-hook all still work. To get
the full shell back:

- `dvt feature add shell-kit` (pulls `homebrew` too), or
- `dvt init --image base-ubuntu` before adding the toolchain feature.

`base-ubuntu` stays published as the batteries-included option; this is a deliberate
"lean by default, opt in to the shell kit" stance, matching the user's intent for the
re-point.

### `dvt init`

`DEFAULT_IMAGE` in `dvt/src/devtemplate/commands/init.py` stays
`ghcr.io/jesserobertson/base-ubuntu:latest` — a bare `dvt init` with no feature should
still give the full shell. Only templates (which always add a toolchain feature) point at
the `-slim` bases.

## `dvt` — dependency awareness

### Data acquisition

**`dvt/src/devtemplate/github.py`** — two new functions mirroring the template pair, same
`@on_err` retry decorator:

- `list_feature_names(client, repo, branch) -> Result[list[str], Exception]` — GETs
  `https://api.github.com/repos/{repo}/contents/features?ref={branch}`, returns sorted dir
  names.
- `fetch_feature_spec(client, repo, branch, feature_id) -> Result[dict, Exception]` — GETs
  `https://raw.githubusercontent.com/{repo}/{branch}/features/{id}/devcontainer-feature.json`.

**`dvt/src/devtemplate/config.py`** — add `features_dir` (sibling of `templates_dir`,
e.g. `<data_dir>/features/`).

**`dvt/src/devtemplate/store.py`** — new `sync_features(settings, client)`, called by the
`sync` command immediately after `sync_templates` (and by `feature add`'s auto-sync
fallback). For every name from `list_feature_names`, fetch the spec and cache a trimmed
record to `features_dir/<id>.json`:

```json
{ "id": "...", "version": "...", "name": "...", "description": "...",
  "dependsOn": ["ghcr.io/.../pixi"], "installsAfter": ["ghcr.io/.../homebrew"] }
```

`dependsOn` is normalised from the spec's object form (`{ "<ref>": {} }`) to a list of
refs. Feature names are validated against the existing `TEMPLATE_NAME_PATTERN` before any
write. The manifest gains a `managed_features` key so pruning removed/renamed features
works exactly like templates.

Request-count impact: ~15 templates + ~16 feature specs per sync (was ~15). Retry wrapper
covers transient failure; a single feature-spec fetch failure aborts the sync (same
contract as a template fetch failure) so the cache never goes half-written.

### Resolver — new module `dvt/src/devtemplate/deps.py`

```python
@dataclass(frozen=True)
class FeatureRecord:                 # one cached features_dir/<id>.json
    id: str
    version: str
    name: str
    description: str
    depends_on: tuple[str, ...]      # normalised list of refs
    installs_after: tuple[str, ...]

@dataclass(frozen=True)
class Resolution:
    feature: str
    pulls_in: tuple[str, ...]        # transitive dependsOn closure, dedup, stable order
    installs_after: tuple[str, ...]  # this feature's own installsAfter refs (not transitive)

def load_feature_cache(settings) -> dict[str, FeatureRecord]: ...
def resolve(feature_id: str, cache: Mapping[str, FeatureRecord]) -> Result[Resolution, Exception]: ...
```

- `resolve` does DFS over `dependsOn`, mapping each ref to a short id
  (`ghcr.io/jesserobertson/devcontainers/pixi` → `pixi`) via the cache. Cycle → `Err`.
- A `dependsOn` ref not in the cache (e.g. an upstream `ghcr.io/devcontainers/...` ref) is
  kept in `pulls_in` as its bare ref and not recursed into — surfaced, not fatal.
- `installsAfter` is **ordering metadata only**: reported for display, never part of the
  closure.
- Pure: cache in, dataclass out, no network. Unit-tested with fixture records.

### UI surfaces

**`dvt feature list`** — new `"Pulls in"` column between Description and Base Image, value
= comma-joined `resolve(name).pulls_in` (`—` when empty or when the feature cache is
absent). `--json` rows gain `"pulls_in": [...]`. `list` must never hard-fail on missing /
unresolvable dep data — a resolve `Err` renders as `—` with the row intact.

**`dvt feature show <name>`** — after the JSON overlay, print a Rich tree rooted at
`<name>`, children = `dependsOn` recursively, each node annotated with its own
`installsAfter` in dim text. `--json` output gains a `"resolved_depends_on": [...]` key
alongside the existing raw overlay.

**`dvt feature deps <name>`** (new command; `dvt feature tree` as an alias) —

- `<name>` given: the graph for that feature. No arg: the whole fleet.
- `--format tree` (default, Rich) `| dot` (Graphviz) `| mermaid` (for docs/README).
- `--json`: `{ "<id>": { "pulls_in": [...], "installs_after": [...] }, ... }`.
- Reuses `resolve`; `dot`/`mermaid` emitters are ~15 lines each over the resolved edges.

**`dvt feature add <name>`** — after `resolve_or_confirm`, before `add_one`, compute
`resolve(resolved).pulls_in`. If non-empty, print `also pulling in: pixi (via dependsOn)`
(stderr, non-JSON) and store `"pulls_in": [...]` on the sidecar `applied` entry so
`dvt info` can show it. Does **not** modify `devcontainer.json`.

### Explicitly out of scope

`dvt feature add` does not inject implied features into `devcontainer.json`. The
devcontainer spec applies `dependsOn` at build time regardless. Injecting them would
mean: deciding lifecycle (when is an implied dep removed?), handling the user also adding
it explicitly, and a merge path for the implied `{}` options — real complexity for a
duplicate of what the build already does. `dvt` *shows* the graph; it doesn't rewrite the
file for it.

### Degradation

A `dvt` that never synced feature specs (upgraded install, offline) has an empty
`features_dir`: the `"Pulls in"` column shows `—`, `feature show` / `feature deps` print
`run 'dvt sync' for dependency info`, `feature list` and `feature add` never hard-fail.

## Testing

### `tests/test_static.py`

- `FEATURES` (the parametrized fixture list) gains `homebrew`, `pixi`, `shell-kit`. They
  pass the existing generic checks: `test_feature_json_has_required_fields`,
  `test_feature_json_id_matches_dir`, `test_install_sh_syntax` (`bash -n`),
  `test_pixi_calls_run_as_dev` / `test_brew_calls_run_as_dev` (already parametrized; the
  new features' `su dev -c` usage satisfies them).
- New targeted checks: `shell-kit` declares `dependsOn` homebrew and `containerEnv.SHELL`;
  `pixi` declares `installsAfter` homebrew+shell-kit and `containerEnv.PIXI_HOME`;
  `homebrew` install.sh has the `-x …/brew` idempotency guard.
- Dockerfile assertions: replace `test_dockerfile_has_core_slim_full_stages` with a
  `core`/`slim`-only version; add `test_dockerfile_has_no_full_stage`;
  `test_dockerfile_core_stage_has_no_cli_bundle` stays and extends to "no `brew install`
  and no Homebrew installer in core".
- Template base-image assertions: the CPU toolchain group now asserts
  `…/base-ubuntu-slim:latest`; GPU group asserts `…/base-cuda-slim:latest`; new group for
  `agent` / `podman` asserts `…/base-ubuntu:latest`; `rust-devtools` / `cpp-devtools`
  assert `…/base-ubuntu-slim:latest` (unchanged) **and** `dependsOn: homebrew` in their
  feature JSON.
- `images/` checks: `base-cuda-slim.json` exists and validates; each
  `images/<bundle>/.devcontainer/devcontainer.json` lists exactly the three plumbing
  feature refs and points `image` at the matching `-slim` ref.
- Every re-pointed template still declares `dependsOn: pixi` on its own feature (indirect —
  assert the feature JSON, not the template).

### `tests/features/`

New `test_homebrew.py`, `test_shell_kit.py`, `test_pixi.py`: option defaults & types,
`containerEnv` keys/values, `dependsOn` / `installsAfter` ref strings, the `homebrew`
idempotency guard, `pixi` writing the fish snippet only under an `-x fish` check.

### `tests/integration/` (CPU; `devcontainer build`-backed)

- Build `images/base-ubuntu` → assert `fish --version`, `brew --version`,
  `pixi --version` all resolve as `dev`; drop a `pyproject.toml` in `/workspace` and
  assert the bash shell-hook activates the env.
- Build `base-ubuntu-slim` directly → assert `fish`, `brew`, `pixi` are all absent.
- Build a throwaway project = `base-ubuntu-slim` + `rust-devtools` feature → assert `brew`
  got pulled in (`dependsOn: homebrew`) and `cargo` resolves.
- Build a throwaway project = `base-ubuntu-slim` + `pixi` feature only → assert `pixi`
  resolves and the bash hook is present but no fish snippet (no fish).

### `dvt/tests/`

- `test_deps.py` (new): `resolve` closure correctness, `installsAfter` excluded from
  closure, cycle → `Err`, unknown-ref kept as bare ref, diamond dedup.
- `store` / `sync`: mocked `httpx` returning a `features/` listing + specs; assert
  `features_dir/*.json` written with normalised `dependsOn`, manifest `managed_features`
  populated, prune on removal.
- `feature list`: `"Pulls in"` column present and correct; `—` when `features_dir` empty;
  `--json` has `pulls_in`.
- `feature show`: tree rendered; `--json` has `resolved_depends_on`.
- `feature deps`: single + fleet; `--format dot` / `mermaid` / `--json` shapes; unknown
  feature name → error.
- `feature add`: prints `also pulling in: …` for a `dependsOn` feature; sidecar entry has
  `pulls_in`; `devcontainer.json` `features` block unchanged (no injection).
- Any existing `dvt` test asserting a template's `image == base-ubuntu` updates to the new
  `-slim` ref.

### `build-images.tests.ps1`

Covered under "Build plumbing" above.

## Rollout

One PR, but landing/CI sequence matters:

1. **`features/{homebrew,shell-kit,pixi}` land first in the merge.** On merge to `main`,
   `publish-features.yml` pushes them to GHCR; tag `feat-homebrew-v1.0.0` etc.
2. **`base/Dockerfile` `full` removal + `images/*` + `build.yml` split.** The
   `build-bundles` job needs the three features published (step 1). If `publish-features`
   and `build.yml` race on the same push, the first `build-bundles` may use stale
   `:latest`; a re-run converges. Non-blocking (see "Feature-publish lag").
3. **`dependsOn` edges on downstream features + template re-points.** Version-bump + tag
   each edited feature.
4. **`dvt` dependency-awareness code** + `dvt` minor version bump.

Steps 2–4 can share the PR with step 1; only the CI ordering of step 1's publish before
the first bundle build matters, and that self-heals.

## Versioning & docs

- New features: `1.0.0`. Edited downstream features: minor bump each, `feat-<id>-v*` tag.
- `dvt`: minor bump (new `feature deps` command, new `list` column, `sync` fetches specs).
  `dvt/CHANGELOG.md` entry.
- Root `CHANGELOG.md` under `## 2026-09-02`: `full` stage dissolved into
  `homebrew`/`shell-kit`/`pixi` features; `base-cuda-slim` added; bundle images now
  assembled via `devcontainer build`; toolchain templates re-pointed to `-slim` bases;
  `dvt` dependency awareness.
- `README.md`: base-image table → four images, note the two `-slim` are the only
  Dockerfile output and the two bundles are assembled from features; feature table gains
  `homebrew` / `shell-kit` / `pixi` rows and a "Pulls in" note; add the "get the full
  shell back on a slim template" tip near the templates section.
- `dvt/README.md` + `dvt/docs/content/quickstart.md`: mention `dvt feature deps` and the
  `"Pulls in"` column.
- `RELEASE.md`: the base-images section notes the two-phase build (`docker build` for
  `-slim`, `devcontainer build` for the bundles) and the feature-publish-before-bundle
  ordering.

## Backward compatibility

- Projects pinned to `image: ghcr.io/jesserobertson/base-ubuntu:latest` (or `base-cuda`)
  keep working — the bundle is still published at the same ref and is functionally
  equivalent to today's `full`.
- Existing `dvt` installs keep working; the `"Pulls in"` column shows `—` until the user
  runs `dvt sync` against the new repo layout.
- Feature refs already published (`…/devcontainers/py-devtools:latest` etc.) are unchanged;
  only their `devcontainer-feature.json` gains a `dependsOn` block, and `:latest`
  consumers pick that up on the next pull. A consumer pinned to an old feature version
  keeps the old (no-`dependsOn`) behaviour and must already be on a `full` base for it to
  work — same as today.

## Risks

- **`devcontainer build` output vs the hand-tuned `full` stage.** Feature installs land as
  separate layers with no `--mount=type=cache` apt sharing, so `base-ubuntu` grows
  somewhat and CI build time rises. Mitigation: GHA layer cache on the slim images covers
  the expensive `core` work; the feature layers are `brew install` + two `curl`s.
  Fallback if it's unacceptable: Approach B from brainstorming (Dockerfile `full` stage
  runs the feature `install.sh` scripts in order) — not chosen, recorded.
- **Feature-publish lag** (detailed above). A plumbing-feature + base change in one push
  can build the bundle against the previous feature `:latest`. Self-heals on re-run; a
  `workflow_run:` chain is the fix if needed.
- **`dependsOn` resolver support.** `dependsOn` is newer than `installsAfter` in the
  devcontainer spec; older `@devcontainers/cli` versions may ignore it. Mitigation: every
  feature that has a `dependsOn` also lists the same ref in `installsAfter`, and the
  re-pointed templates are exercised by the CPU integration tests against the pinned CLI
  version CI installs.
- **`slim` with no Homebrew breaks any consumer that assumed brew was there.** Only
  `rust-devtools` / `cpp-devtools` did; both get `dependsOn: homebrew` in this change.
  A third-party project layering its own `brew install` onto `base-ubuntu-slim` would
  break — call this out in the CHANGELOG.
- **`dvt sync` request volume doubles.** ~31 GitHub requests per sync. Unauthenticated
  GitHub API is 60 req/hr/IP — a user running `sync` repeatedly could hit it. Mitigation:
  the raw.githubusercontent.com fetches (the bulk) don't count against the API limit; only
  the two `contents/` directory listings do. Acceptable; note it.
- **`build-images.ps1` / `build.yml` divergence** — now two build tools *and* two build
  mechanisms (docker + devcontainer). `build-images.tests.ps1` guards the PS side;
  `build.yml` is only covered by CI actually running. Same accepted risk as 2026-08-30,
  slightly larger surface.
