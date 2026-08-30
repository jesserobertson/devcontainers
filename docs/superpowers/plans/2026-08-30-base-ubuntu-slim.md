# base-ubuntu-slim + Homebrew-based cpp-devtools / rust-devtools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pixi-free `base-ubuntu-slim` image via a multi-stage `base/Dockerfile`, and move `cpp-devtools` / `rust-devtools` from `pixi global install` to Homebrew, re-basing their templates onto the slim image.

**Architecture:** `base/Dockerfile` becomes three stages — `core` (apt basics, `dev` user, Homebrew installed, chezmoi dotfiles, bash shell, no pixi), `slim` (`FROM core`, nothing added), `full` (`FROM core` + the 12-formula Homebrew CLI bundle + pixi + pixi shell-hooks + fish login shell). `base-ubuntu` and `base-cuda` build `--target full` (behaviourally identical to today); `base-ubuntu-slim` builds `--target slim`. The two native-toolchain features install via `su dev -c 'brew install …'` instead of pixi.

**Tech Stack:** Docker multi-stage builds, devcontainer Features (bash `install.sh`), PowerShell + Pester (`build-images.ps1`), GitHub Actions (`build.yml`), pytest (`tests/test_static.py`), pixi (test runner only: `pixi run pytest`).

**Spec:** `docs/superpowers/specs/2026-08-30-base-ubuntu-slim-design.md`

## Global Constraints

- **Test runner:** `pixi run pytest tests/test_static.py` from the repo root. The full static suite must be green at the end of every task before committing.
- **`full` stage = today's behaviour, unchanged.** The `full` stage keeps the exact 12 Homebrew formulae (`bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide`), the pixi install, the pixi `shell-hook` lines verbatim, and `chsh` to fish. No `helix` added to any base stage.
- **Homebrew refuses root.** Every `brew install` in a feature `install.sh` runs on the same line as `su dev -c '…'` (Homebrew binary path: `/home/linuxbrew/.linuxbrew/bin/brew`). The base image already exports `HOMEBREW_NO_AUTO_UPDATE=1` / `HOMEBREW_NO_ANALYTICS=1` as image ENV, so feature scripts do not repeat them.
- **Feature version bumps are mandatory when `install.sh` changes** — `devcontainers/action` publishing is version-keyed. `cpp-devtools` and `rust-devtools` both go to `1.1.0`.
- **Image ref:** `ghcr.io/jesserobertson/base-ubuntu-slim:latest`. **dvt aliases:** `["ubuntu-slim", "slim"]`.
- **New image name** must match `^[a-z0-9][a-z0-9-]*$` (dvt's `validate_image_name`). `base-ubuntu-slim` does.
- `test_published_feature_version_matches_local_content` reaching GHCR may `skip` (network) or `pass` (bumped version returns 404 → `None`). Neither is a failure.

---

### Task 1: Multi-stage `base/Dockerfile`

**Files:**
- Modify: `base/Dockerfile` (full rewrite into three stages)
- Modify: `tests/test_static.py` (add a stage-slicing helper + 5 stage tests near the existing `--- base Dockerfile ---` section, ~line 300)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `base/Dockerfile` with stages named exactly `core`, `slim`, `full`. `full` is the last stage (bare `docker build base/` still yields it). `tests/test_static.py` gains `_dockerfile_stage(name: str) -> str`.

- [ ] **Step 1: Add the failing stage tests**

In `tests/test_static.py`, find the section headed `# --- base Dockerfile ---` (defines `_dockerfile_text()`). Immediately **after** the existing `def test_dockerfile_sets_pixi_home():` function, add:

```python
def _dockerfile_stage(name: str) -> str:
    """Return the text of one multi-stage build stage: the `FROM … AS <name>`
    line through to (not including) the next top-level `FROM` line."""
    lines = _dockerfile_text().splitlines()
    start = next(
        (i for i, l in enumerate(lines)
         if re.match(rf"^FROM\s+\S+\s+AS\s+{re.escape(name)}\s*$", l)),
        None,
    )
    assert start is not None, f"no Dockerfile stage named {name!r}"
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].startswith("FROM ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_dockerfile_has_core_slim_full_stages():
    text = _dockerfile_text()
    assert re.search(r"^FROM \S+ AS core$", text, re.M)
    assert re.search(r"^FROM core AS slim$", text, re.M)
    assert re.search(r"^FROM core AS full$", text, re.M)


def test_dockerfile_core_stage_is_pixi_free():
    core = _dockerfile_stage("core")
    assert "pixi.sh/install.sh" not in core
    assert "PIXI_HOME" not in core


def test_dockerfile_core_stage_has_no_cli_bundle():
    assert "brew install" not in _dockerfile_stage("core")


def test_dockerfile_slim_stage_adds_nothing():
    assert "RUN" not in _dockerfile_stage("slim")


def test_dockerfile_full_stage_has_pixi_and_cli_bundle():
    full = _dockerfile_stage("full")
    assert "pixi.sh/install.sh" in full
    assert 'ENV PIXI_HOME="/home/dev/.local/share/pixi"' in full
    assert "brew install" in full
```

`re` is already imported at the top of the file.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pixi run pytest tests/test_static.py -q -k "dockerfile"`
Expected: the 5 new `test_dockerfile_*` tests FAIL (`no Dockerfile stage named 'core'`), the 4 pre-existing `test_dockerfile_*` tests PASS.

- [ ] **Step 3: Rewrite `base/Dockerfile` as three stages**

Replace the **entire** contents of `base/Dockerfile` with:

```dockerfile
ARG BASE_IMAGE=ubuntu:24.04

# ─────────────────────────────────────────────────────────────────────────────
# core — shared by every variant. No pixi, no Homebrew CLI bundle, no fish.
# Published (as-is) as base-ubuntu-slim via the `slim` stage below.
# ─────────────────────────────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS core

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
    build-essential procps curl file git wget unzip sudo

# One dedicated non-root user for the whole image. Homebrew refuses to run
# as root anyway, and containment for features/agent requires a non-root
# runtime user regardless. No passwordless sudo: the boundary must hold
# even if this user's session is compromised — see features/agent for the
# one narrowly-scoped exception it adds for itself, nothing else.
RUN useradd -m -s /bin/bash dev

# Homebrew's Linux installer always installs to the hardcoded prefix
# /home/linuxbrew/.linuxbrew regardless of which user runs it. That
# directory no longer matches dev's home (/home/dev), and /home itself is
# root-owned, so pre-create it and hand ownership to dev before running the
# installer as dev - without this, the installer would need sudo to create
# it, which dev intentionally does not have.
RUN mkdir -p /home/linuxbrew && chown dev:dev /home/linuxbrew
RUN su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/dev/.local/bin:$PATH"
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_NO_ANALYTICS=1
ENV UV_HTTP_TIMEOUT=300

# Dotfiles land in core (not full) because they degrade gracefully when the
# CLI bundle and the Python toolchain aren't present:
# dot_config/fish/functions/init_cached.fish and dot_bashrc.tmpl's
# _init_cached both `command -v` each tool and no-op when it's missing, and
# PATH additions are existence-checked. `--exclude=scripts` means no run_*
# install script executes here.
RUN curl -fsLS get.chezmoi.io | sh -s -- -b /usr/local/bin
RUN su dev -c 'GIT_TERMINAL_PROMPT=0 chezmoi init --apply --no-tty --exclude=scripts \
    https://github.com/jesserobertson/dotfiles.git'

RUN HOME=/home/dev git config --global --add safe.directory /workspace && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev

# ─────────────────────────────────────────────────────────────────────────────
# slim — core with nothing added. This is what base-ubuntu-slim publishes.
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS slim

# ─────────────────────────────────────────────────────────────────────────────
# full — core + Homebrew CLI bundle + pixi + pixi shell-hooks + fish login
# shell. base-ubuntu builds this (--target full); base-cuda builds it with a
# CUDA BASE_IMAGE. Behaviourally identical to the pre-multi-stage Dockerfile.
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS full
USER root

RUN su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

# The devcontainers/dotfiles repo redirects PIXI_HOME to ~/.local/share/pixi
# (avoiding a bare ~/.pixi dir, same as CARGO_HOME/RUSTUP_HOME) via a regular
# dotfiles config file - but Docker RUN steps don't source shell profiles,
# and chezmoi apply already ran back in the core stage. Set it explicitly so
# pixi's installer places the binary at the same path every other pixi
# invocation in this image (including every feature's install.sh) looks for.
ENV PIXI_HOME="/home/dev/.local/share/pixi"
RUN curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash

ENV PATH="/home/dev/.local/share/pixi/bin:$PATH"
ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish

RUN chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev && \
    mkdir -p /home/dev/.config/fish/conf.d && \
    printf 'if status is-interactive\n    if test -f /workspace/pixi.toml; or test -f /workspace/pyproject.toml\n        eval (pixi shell-hook --manifest-path /workspace --shell fish)\n    end\nend\n' \
    > /home/dev/.config/fish/conf.d/project-pixi.fish && \
    echo 'if [ -f /workspace/pixi.toml ] || [ -f /workspace/pyproject.toml ]; then eval "$(pixi shell-hook --manifest-path /workspace --shell bash)"; fi' >> /home/dev/.bashrc && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev
```

- [ ] **Step 4: Run the Dockerfile tests to verify they pass**

Run: `pixi run pytest tests/test_static.py -q -k "dockerfile"`
Expected: all 9 `test_dockerfile_*` tests PASS (5 new + 4 pre-existing: `test_dockerfile_creates_dev_user`, `test_dockerfile_no_passwordless_sudo`, `test_dockerfile_ends_as_dev_user`, `test_dockerfile_sets_pixi_home`).

- [ ] **Step 5: Run the full static suite**

Run: `pixi run pytest tests/test_static.py -q`
Expected: PASS (1 skip on the GHCR drift check is acceptable).

- [ ] **Step 6: Commit**

```bash
git add base/Dockerfile tests/test_static.py
git commit -m "refactor(base): split base/Dockerfile into core/slim/full stages

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `base-ubuntu-slim` image registry entry

**Files:**
- Create: `images/base-ubuntu-slim.json`
- Modify: `tests/test_static.py` (the `# --- images/ registry ---` section, ~line 360: extend `IMAGES`, add a ref test)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `images/base-ubuntu-slim.json` with `ref == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"`. `IMAGES` list in `tests/test_static.py` includes `"base-ubuntu-slim"`.

- [ ] **Step 1: Add the failing tests**

In `tests/test_static.py`, in the `# --- images/ registry ---` section, change:

```python
IMAGES = ["base-ubuntu", "base-cuda"]
```

to:

```python
IMAGES = ["base-ubuntu", "base-cuda", "base-ubuntu-slim"]
```

Then, immediately after `def test_base_cuda_ref_matches_gpu_templates():`, add:

```python
def test_base_ubuntu_slim_ref():
    assert (
        _image_json("base-ubuntu-slim")["ref"]
        == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -q -k "image"`
Expected: `test_base_ubuntu_slim_ref`, plus the parametrized `test_image_json_has_required_fields[base-ubuntu-slim]` and `test_image_json_name_matches_filename[base-ubuntu-slim]`, FAIL with `FileNotFoundError` / missing file.

- [ ] **Step 3: Create `images/base-ubuntu-slim.json`**

```json
{
  "name": "base-ubuntu-slim",
  "description": "Ubuntu 24.04 devcontainer base with Homebrew and the chezmoi dotfiles but no pixi and no CLI bundle - a lean base for toolchains that install via brew or apt.",
  "ref": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
  "aliases": ["ubuntu-slim", "slim"]
}
```

- [ ] **Step 4: Run to verify pass**

Run: `pixi run pytest tests/test_static.py -q -k "image"`
Expected: PASS (all `test_image_*` including the three `[base-ubuntu-slim]` params).

- [ ] **Step 5: Full suite + commit**

```bash
pixi run pytest tests/test_static.py -q
git add images/base-ubuntu-slim.json tests/test_static.py
git commit -m "feat(images): add base-ubuntu-slim registry entry

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Build plumbing — `--target` in `build-images.ps1` + `build.yml`

**Files:**
- Modify: `build-images.ps1` (`$ImageDefs`, the local-build `$buildArgs` assembly, the `-Images` `[ValidateSet]` + default, the `.PARAMETER Images` doc)
- Modify: `build-images.tests.ps1` (new assertions in `Describe 'Invoke-Build — image build mode'`)
- Modify: `.github/workflows/build.yml` (matrix `include` entries + the `docker/build-push-action` step)

**Interfaces:**
- Consumes: `base/Dockerfile` stages `full` / `slim` from Task 1.
- Produces: `build-images.ps1` builds `base-ubuntu` and `base-cuda` with `--target full`, `base-ubuntu-slim` with `--target slim`. `build.yml` matrix has a `base-ubuntu-slim` row and passes `target:` to `build-push-action`.

- [ ] **Step 1: Add failing Pester assertions**

In `build-images.tests.ps1`, inside `Describe 'Invoke-Build — image build mode'` (after the `It 'passes BASE_IMAGE build-arg for base-cuda'` block), add:

```powershell
    It 'builds base-ubuntu with --target full' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu') }

        Should -Invoke docker -ParameterFilter {
            ($args -join ' ') -match '--target\s+full'
        }
    }

    It 'builds base-ubuntu-slim with --target slim' {
        Invoke-BuildDefault @{ SkipFeatures = $true; Images = @('base-ubuntu-slim') }

        Should -Invoke docker -ParameterFilter {
            $args -contains 'ghcr.io/jesserobertson/base-ubuntu-slim:latest' -and
            ($args -join ' ') -match '--target\s+slim'
        }
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `pwsh -NoProfile -Command "Invoke-Pester ./build-images.tests.ps1 -Output Detailed"`
Expected: the two new `It` blocks FAIL (`base-ubuntu-slim` not in the `-Images` ValidateSet → parameter binding error; and no `--target` in args).
If `Invoke-Pester` is unavailable in this environment, instead run
`pwsh -NoProfile -Command ". ./build-images.ps1; \$ImageDefs['base-ubuntu-slim']"` and expect it to print nothing (key absent) — that is the equivalent "red".

- [ ] **Step 3: Edit `build-images.ps1`**

3a. In the `param(...)` block, change the `-Images` validation + default from:

```powershell
    [ValidateSet('base-ubuntu', 'base-cuda', 'ramalama')]
    [string[]]$Images = @('base-ubuntu', 'base-cuda', 'ramalama'),
```

to:

```powershell
    [ValidateSet('base-ubuntu', 'base-ubuntu-slim', 'base-cuda', 'ramalama')]
    [string[]]$Images = @('base-ubuntu', 'base-ubuntu-slim', 'base-cuda', 'ramalama'),
```

3b. In the `.PARAMETER Images` doc comment, change `Defaults to all: base-ubuntu, base-cuda, ramalama.` to `Defaults to all: base-ubuntu, base-ubuntu-slim, base-cuda, ramalama.`

3c. In `$ImageDefs`, add a `Target` key to the two Ubuntu-family entries and the CUDA entry, and insert the new `base-ubuntu-slim` entry directly after `base-ubuntu`:

```powershell
    $ImageDefs = [ordered]@{
        'base-ubuntu' = @{
            Context   = 'base'
            Target    = 'full'
            BuildArgs = @{ BASE_IMAGE = 'ubuntu:24.04' }
            Tags      = @("$Registry/$Owner/base-ubuntu:latest")
        }
        'base-ubuntu-slim' = @{
            Context   = 'base'
            Target    = 'slim'
            BuildArgs = @{ BASE_IMAGE = 'ubuntu:24.04' }
            Tags      = @("$Registry/$Owner/base-ubuntu-slim:latest")
        }
        'base-cuda' = @{
            Context   = 'base'
            Target    = 'full'
            BuildArgs = @{ BASE_IMAGE = 'nvidia/cuda:12.8.0-devel-ubuntu24.04' }
            Tags      = @(
                "$Registry/$Owner/base-cuda:latest",
                "$Registry/$Owner/base-cuda:cuda12.8.0"
            )
        }
        'ramalama' = @{
            Context   = 'ramalama'
            BuildArgs = @{}
            Tags      = @("$Registry/$Owner/ramalama:latest")
        }
    }
```

3d. In the local-build branch (the `else` of `if ($Pull)`), where `$buildArgs` is assembled, change:

```powershell
                $buildArgs = @('build')
                foreach ($tag in $def.Tags)                       { $buildArgs += '--tag', $tag }
                foreach ($kv in $def.BuildArgs.GetEnumerator())   { $buildArgs += '--build-arg', "$($kv.Key)=$($kv.Value)" }
                $buildArgs += $def.Context
```

to:

```powershell
                $buildArgs = @('build')
                foreach ($tag in $def.Tags)                       { $buildArgs += '--tag', $tag }
                foreach ($kv in $def.BuildArgs.GetEnumerator())   { $buildArgs += '--build-arg', "$($kv.Key)=$($kv.Value)" }
                if ($def.Target)                                  { $buildArgs += '--target', $def.Target }
                $buildArgs += $def.Context
```

(The `$Features` `[ValidateSet]` lower in the `param()` block is already stale and out of scope — leave it untouched.)

- [ ] **Step 4: Edit `.github/workflows/build.yml`**

Change the matrix `include:` block from:

```yaml
        include:
          - name: base-ubuntu
            base_image: ubuntu:24.04
            tags: ghcr.io/jesserobertson/base-ubuntu:latest
          - name: base-cuda
            base_image: nvidia/cuda:12.8.0-devel-ubuntu24.04
            tags: |
              ghcr.io/jesserobertson/base-cuda:latest
              ghcr.io/jesserobertson/base-cuda:cuda12.8.0
```

to:

```yaml
        include:
          - name: base-ubuntu
            base_image: ubuntu:24.04
            target: full
            tags: ghcr.io/jesserobertson/base-ubuntu:latest
          - name: base-ubuntu-slim
            base_image: ubuntu:24.04
            target: slim
            tags: ghcr.io/jesserobertson/base-ubuntu-slim:latest
          - name: base-cuda
            base_image: nvidia/cuda:12.8.0-devel-ubuntu24.04
            target: full
            tags: |
              ghcr.io/jesserobertson/base-cuda:latest
              ghcr.io/jesserobertson/base-cuda:cuda12.8.0
```

And in the `docker/build-push-action@v7` step, add a `target:` line to `with:` (place it right after `context: base`):

```yaml
      - uses: docker/build-push-action@v7
        with:
          context: base
          target: ${{ matrix.target }}
          push: true
          build-args: BASE_IMAGE=${{ matrix.base_image }}
          tags: ${{ matrix.tags }}
          cache-from: type=gha,scope=${{ matrix.name }}
          cache-to: type=gha,mode=max,scope=${{ matrix.name }}
```

- [ ] **Step 5: Run to verify pass**

Run: `pwsh -NoProfile -Command "Invoke-Pester ./build-images.tests.ps1 -Output Detailed"`
Expected: all `It` blocks PASS, including the two new ones. (Fallback if no Pester: `pwsh -NoProfile -Command ". ./build-images.ps1; \$ImageDefs['base-ubuntu-slim'].Target"` prints `slim`.)

Also sanity-check the YAML parses:
Run: `pixi run python -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml'))"`
Expected: no output, exit 0.

- [ ] **Step 6: Full static suite + commit**

```bash
pixi run pytest tests/test_static.py -q
git add build-images.ps1 build-images.tests.ps1 .github/workflows/build.yml
git commit -m "build: build base-ubuntu-slim via --target slim

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `rust-devtools` → Homebrew

**Files:**
- Modify: `features/rust-devtools/install.sh`
- Modify: `features/rust-devtools/devcontainer-feature.json` (`version`, `description`)
- Modify: `tests/test_static.py` (add `BREW_FEATURES` + `test_brew_calls_run_as_dev`; drop `"rust-devtools"` from `SU_DEV_FEATURES`)

**Interfaces:**
- Consumes: nothing from Tasks 1–3.
- Produces: `BREW_FEATURES = ["rust-devtools"]` in `tests/test_static.py`; `test_brew_calls_run_as_dev(feature)` parametrized over it. `SU_DEV_FEATURES` no longer contains `"rust-devtools"`.

- [ ] **Step 1: Add the failing test**

In `tests/test_static.py`, directly after the existing `def test_pixi_calls_run_as_dev(feature):` function, add:

```python
BREW_FEATURES = ["rust-devtools"]


@pytest.mark.parametrize("feature", BREW_FEATURES)
def test_brew_calls_run_as_dev(feature):
    script = (REPO_ROOT / "features" / feature / "install.sh").read_text()
    brew_lines = [l for l in script.splitlines() if "brew install" in l]
    assert brew_lines, f"{feature}: no 'brew install' line in install.sh"
    for line in brew_lines:
        assert "su dev -c" in line, f"{feature}: brew install not via su dev -c: {line!r}"
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -q -k "brew_calls_run_as_dev"`
Expected: FAIL — `rust-devtools: no 'brew install' line in install.sh` (it still uses `pixi global install`).

- [ ] **Step 3: Rewrite `features/rust-devtools/install.sh`**

Replace the whole file with:

```bash
#!/bin/bash
set -e

su dev -c '/home/linuxbrew/.linuxbrew/bin/brew install rust rust-analyzer helix'
```

- [ ] **Step 4: Update `features/rust-devtools/devcontainer-feature.json`**

Replace the whole file with:

```json
{
  "id": "rust-devtools",
  "version": "1.1.0",
  "name": "Rust dev tooling (cargo, rust-analyzer, helix)",
  "description": "Installs the Rust toolchain (rustc, cargo), rust-analyzer, and the Helix editor via Homebrew. Helix's own default language config already pairs Rust with rust-analyzer, so no extra wiring is needed. Designed for the base-ubuntu-slim image; also works on base-ubuntu and base-cuda.",
  "options": {}
}
```

- [ ] **Step 5: Drop `rust-devtools` from `SU_DEV_FEATURES`**

In `tests/test_static.py`, change:

```python
SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
    "rust-devtools", "cpp-devtools",
]
```

to:

```python
SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
    "cpp-devtools",
]
```

- [ ] **Step 6: Run to verify pass**

Run: `pixi run pytest tests/test_static.py -q -k "rust-devtools or brew_calls_run_as_dev or install_sh_syntax"`
Expected: PASS — `test_brew_calls_run_as_dev[rust-devtools]`, `test_install_sh_syntax[rust-devtools]`, `test_feature_json_has_required_fields[rust-devtools]`, `test_feature_json_id_matches_dir[rust-devtools]` all green; `test_pixi_calls_run_as_dev[rust-devtools]` no longer collected.

- [ ] **Step 7: Full suite + commit**

```bash
pixi run pytest tests/test_static.py -q
git add features/rust-devtools/ tests/test_static.py
git commit -m "feat(rust-devtools): install via Homebrew instead of pixi (v1.1.0)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `cpp-devtools` → Homebrew

**Files:**
- Modify: `features/cpp-devtools/install.sh`
- Modify: `features/cpp-devtools/devcontainer-feature.json` (`version`, `name`, `description`)
- Modify: `tests/test_static.py` (`BREW_FEATURES` += `"cpp-devtools"`; drop `"cpp-devtools"` from `SU_DEV_FEATURES`)

**Interfaces:**
- Consumes: `BREW_FEATURES` and `test_brew_calls_run_as_dev` from Task 4.
- Produces: `BREW_FEATURES == ["rust-devtools", "cpp-devtools"]`; `SU_DEV_FEATURES` no longer contains `"cpp-devtools"` (so it is now back to the 11-entry Python/GPU list).

- [ ] **Step 1: Extend the parametrized test**

In `tests/test_static.py`, change:

```python
BREW_FEATURES = ["rust-devtools"]
```

to:

```python
BREW_FEATURES = ["rust-devtools", "cpp-devtools"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -q -k "brew_calls_run_as_dev"`
Expected: `test_brew_calls_run_as_dev[cpp-devtools]` FAILS — `cpp-devtools: no 'brew install' line in install.sh` (still `pixi global install`).

- [ ] **Step 3: Rewrite `features/cpp-devtools/install.sh`**

Replace the whole file with:

```bash
#!/bin/bash
set -e

su dev -c '/home/linuxbrew/.linuxbrew/bin/brew install llvm cmake ninja ccache pkgconf helix'

# Homebrew's llvm is keg-only: clang/clang++/clangd/lld/lldb/clang-format/
# clang-tidy live in opt/llvm/bin and are NOT symlinked onto PATH. Add that
# dir for the dev user so `clang` and `clangd` (Helix's default C/C++ LSP)
# resolve. `make` comes from the base image's build-essential. Written as
# root then chowned - same pattern as py-devtools' languages.toml.
LLVM_BIN=/home/linuxbrew/.linuxbrew/opt/llvm/bin
mkdir -p /home/dev/.config/fish/conf.d
echo "fish_add_path -gp $LLVM_BIN" > /home/dev/.config/fish/conf.d/cpp-devtools.fish
echo "export PATH=\"$LLVM_BIN:\$PATH\"" >> /home/dev/.bashrc
chown dev:dev /home/dev/.config/fish/conf.d/cpp-devtools.fish
```

- [ ] **Step 4: Update `features/cpp-devtools/devcontainer-feature.json`**

Replace the whole file with:

```json
{
  "id": "cpp-devtools",
  "version": "1.1.0",
  "name": "C/C++ dev tooling (clang, cmake, ninja, lldb, helix)",
  "description": "Installs a C/C++ toolchain via Homebrew: clang/clang++, clangd, lld, lldb, cmake, ninja, ccache, pkgconf, and the Helix editor. Adds Homebrew's keg-only llvm bin dir to PATH so clang and clangd resolve; Helix's default config already pairs C/C++ with clangd. Designed for the base-ubuntu-slim image; also works on base-ubuntu and base-cuda.",
  "options": {}
}
```

- [ ] **Step 5: Drop `cpp-devtools` from `SU_DEV_FEATURES`**

In `tests/test_static.py`, change:

```python
SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
    "cpp-devtools",
]
```

to:

```python
SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
]
```

- [ ] **Step 6: Run to verify pass**

Run: `pixi run pytest tests/test_static.py -q -k "cpp-devtools or brew_calls_run_as_dev"`
Expected: PASS — `test_brew_calls_run_as_dev[cpp-devtools]`, `test_install_sh_syntax[cpp-devtools]` (`bash -n` accepts the escaped `echo`), `test_feature_json_*[cpp-devtools]` all green.

- [ ] **Step 7: Full suite + commit**

```bash
pixi run pytest tests/test_static.py -q
git add features/cpp-devtools/ tests/test_static.py
git commit -m "feat(cpp-devtools): install via Homebrew instead of pixi (v1.1.0)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Re-base `cpp-devtools` / `rust-devtools` templates onto `base-ubuntu-slim`

**Files:**
- Modify: `templates/cpp-devtools/devcontainer.json`
- Modify: `templates/rust-devtools/devcontainer.json`
- Modify: `tests/test_static.py` (add `SLIM_TEMPLATE_FEATURES` + its 4 parametrized tests; remove the two features from `CPU_TEMPLATE_FEATURES`; repoint `test_template_post_create_enables_detached_environments` at a filtered list)

**Interfaces:**
- Consumes: `base-ubuntu-slim` ref string (Task 2); `FEATURES` list (already defines both features).
- Produces: `SLIM_TEMPLATE_FEATURES = ["rust-devtools", "cpp-devtools"]`; `PIXI_TEMPLATE_FEATURES` = `FEATURES` minus those two, used by the detached-environments test.

- [ ] **Step 1: Add the failing template tests**

In `tests/test_static.py`, at the top-of-file list block (right after `CPU_TEMPLATE_FEATURES = [...]`), add:

```python
SLIM_TEMPLATE_FEATURES = ["rust-devtools", "cpp-devtools"]

# Templates whose postCreateCommand must set pixi detached-environments -
# everything except the slim-based ones, which run no pixi at all.
PIXI_TEMPLATE_FEATURES = [f for f in FEATURES if f not in SLIM_TEMPLATE_FEATURES]
```

In the same block, change:

```python
CPU_TEMPLATE_FEATURES = [
    "marimo", "fastapi", "cli", "py-devtools", "huggingface", "ollama", "podman",
    "rust-devtools", "cpp-devtools",
]
```

to:

```python
CPU_TEMPLATE_FEATURES = [
    "marimo", "fastapi", "cli", "py-devtools", "huggingface", "ollama", "podman",
]
```

In the `# --- templates/ (standalone per-feature devcontainer.json) ---` section, after the block of `test_cpu_template_*` functions, add:

```python
@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_uses_base_ubuntu_slim(feature):
    assert (
        _template_json(feature)["image"]
        == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"
    )


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))
```

Finally, change the decorator on `test_template_post_create_enables_detached_environments` from:

```python
@pytest.mark.parametrize("feature", FEATURES)
def test_template_post_create_enables_detached_environments(feature):
```

to:

```python
@pytest.mark.parametrize("feature", PIXI_TEMPLATE_FEATURES)
def test_template_post_create_enables_detached_environments(feature):
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -q -k "slim_template or detached_environments"`
Expected: `test_slim_template_uses_base_ubuntu_slim[*]` FAIL (templates still say `base-ubuntu:latest`); `test_template_post_create_enables_detached_environments` no longer parametrized with `rust-devtools` / `cpp-devtools`.

- [ ] **Step 3: Rewrite `templates/cpp-devtools/devcontainer.json`**

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

- [ ] **Step 4: Rewrite `templates/rust-devtools/devcontainer.json`**

```json
{
  "name": "rust-devtools",
  "description": "Rust toolchain (cargo, rustc) plus rust-analyzer and Helix, via Homebrew on the slim base.",
  "image": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/rust-devtools:latest": {}
  },
  "remoteUser": "dev"
}
```

- [ ] **Step 5: Run to verify pass**

Run: `pixi run pytest tests/test_static.py -q -k "template"`
Expected: PASS — the 4 new `test_slim_template_*[rust-devtools|cpp-devtools]` green; `test_cpu_template_*` no longer collected for those two; `test_template_post_create_enables_detached_environments` green for the filtered list.

- [ ] **Step 6: Full suite + commit**

```bash
pixi run pytest tests/test_static.py -q
git add templates/cpp-devtools/ templates/rust-devtools/ tests/test_static.py
git commit -m "feat(templates): base cpp-devtools/rust-devtools on base-ubuntu-slim

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md` (Base images table; the `rust-devtools` / `cpp-devtools` rows of the Features table; the `features/` tree lines)
- Modify: `CHANGELOG.md` (the `## 2026-08-30` section)
- Modify: `RELEASE.md` (the `## Base images (base/Dockerfile)` section)
- Modify: `tests/test_static.py` (one new README guard test)

**Interfaces:**
- Consumes: everything prior (documents it).
- Produces: no new code interfaces.

- [ ] **Step 1: Add the failing README guard**

In `tests/test_static.py`, right after `def test_readme_documents_agent():`, add:

```python
def test_readme_documents_base_ubuntu_slim():
    assert "base-ubuntu-slim" in (REPO_ROOT / "README.md").read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -q -k "readme"`
Expected: `test_readme_documents_base_ubuntu_slim` FAILS.

- [ ] **Step 3: Update `README.md` — Base images table**

Change:

```
| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |
```

to:

```
| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects — fish, Homebrew CLI kit, pixi |
| `ghcr.io/jesserobertson/base-ubuntu-slim:latest` | `ubuntu:24.04` | Lean CPU base — Homebrew + dotfiles only, no pixi or CLI kit (used by `rust-devtools`, `cpp-devtools`) |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |
```

- [ ] **Step 4: Update `README.md` — Features table rows**

Change the `rust-devtools` row to:

```
| `…/rust-devtools:latest` | Dev | base-ubuntu-slim | Rust dev tooling via Homebrew — cargo, rustc, rust-analyzer, Helix editor (Helix's default config already pairs Rust with rust-analyzer) |
```

Change the `cpp-devtools` row to:

```
| `…/cpp-devtools:latest` | Dev | base-ubuntu-slim | C/C++ dev tooling via Homebrew — clang, clangd, lld, lldb, cmake, ninja, ccache, pkgconf, Helix editor (Helix's default config already pairs C/C++ with clangd) |
```

- [ ] **Step 5: Update `README.md` — `features/` tree lines**

Change:

```
  rust-devtools/             ← Dev: cargo, rustc, rust-analyzer, Helix
  cpp-devtools/              ← Dev: clang, cmake, ninja, lldb/gdb, clangd, Helix
```

to:

```
  rust-devtools/             ← Dev: cargo, rustc, rust-analyzer, Helix (brew)
  cpp-devtools/              ← Dev: clang, clangd, cmake, ninja, lldb, Helix (brew)
```

- [ ] **Step 6: Update `CHANGELOG.md`**

In the `## 2026-08-30` section, replace the existing `cpp-devtools` bullet under `### Added` with:

```markdown
- `base-ubuntu-slim` base image - Ubuntu 24.04 with Homebrew and the chezmoi dotfiles but
  no pixi and no Homebrew CLI bundle, built from a new `core` stage in `base/Dockerfile`.
  `base-ubuntu` and `base-cuda` are unchanged (they build the `full` stage).
- `cpp-devtools` feature - C/C++ toolchain (clang/clang++, clangd, lld, lldb, cmake, ninja,
  ccache, pkgconf) plus the Helix editor, installed via Homebrew. Adds Homebrew's keg-only
  `llvm` bin dir to PATH so `clang`/`clangd` resolve; Helix's default config already pairs
  C/C++ with clangd.
```

And add a `### Changed` subsection to the `## 2026-08-30` section (after `### Added`):

```markdown
### Changed

- `rust-devtools` and `cpp-devtools` now install via Homebrew instead of `pixi global
  install`, and their templates target the new `base-ubuntu-slim` image. Both bumped to
  `1.1.0`. The features still work on `base-ubuntu` / `base-cuda` (Homebrew is present in
  all three).
```

- [ ] **Step 7: Update `RELEASE.md`**

In the `## Base images (`base/Dockerfile`)` section, after the first paragraph (ending "…touches `base/Dockerfile`."), add:

```markdown

`base/Dockerfile` is multi-stage: `base-ubuntu` and `base-cuda` build `--target full`,
`base-ubuntu-slim` builds `--target slim` (a pixi-free `core` stage). All three are in the
`build.yml` matrix and in `build-images.ps1`'s `$ImageDefs`.
```

- [ ] **Step 8: Run to verify pass + full suite**

Run: `pixi run pytest tests/test_static.py -q`
Expected: PASS (1 acceptable GHCR-drift skip). Confirm `test_readme_documents_base_ubuntu_slim`, `test_readme_documents_agent`, `test_readme_no_root_remote_user` all green.

- [ ] **Step 9: Commit**

```bash
git add README.md CHANGELOG.md RELEASE.md tests/test_static.py
git commit -m "docs: document base-ubuntu-slim and the Homebrew cpp/rust-devtools

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Post-plan verification (not a task — run before pushing)

- [ ] `pixi run pytest tests/test_static.py -q` — full green (1 GHCR skip OK).
- [ ] `pixi run pytest tests/ -q` — the rest of the suite (`tests/features/`, `tests/integration/`) unaffected.
- [ ] `pwsh -NoProfile -Command "Invoke-Pester ./build-images.tests.ps1"` — green if Pester present.
- [ ] `git log --oneline origin/main..HEAD` — 7 commits, one per task.
- [ ] Optional local Docker smoke (needs a Linux Docker host; not available in the Windows dev env): `docker build --target slim -t slim-test base/ && docker run --rm slim-test bash -lc 'command -v pixi; command -v brew; command -v fish'` — expect `pixi` and `fish` absent, `brew` present.

## Self-Review

**Spec coverage:**
- Multi-stage `base/Dockerfile` (core/slim/full) → Task 1. ✅
- "no `helix` in base", "`full` == today" → Task 1 (Global Constraints + exact Dockerfile). ✅
- dotfiles kept in core, safety rationale → Task 1 (comment in Dockerfile). ✅
- `images/base-ubuntu-slim.json` → Task 2. ✅
- `build.yml` `target:` + matrix row → Task 3. ✅
- `build-images.ps1` `Target` key + `--target` + ValidateSet → Task 3. ✅
- `build-images.tests.ps1` assertions → Task 3. ✅
- `rust-devtools` → brew, v1.1.0 → Task 4. ✅
- `cpp-devtools` → brew + keg-only PATH snippet, v1.1.0 → Task 5. ✅
- Templates re-based to slim, drop postCreate/mount → Task 6. ✅
- `tests/test_static.py`: `IMAGES` (T2), `SLIM_TEMPLATE_FEATURES` + 4 tests (T6), `BREW_FEATURES` + `test_brew_calls_run_as_dev` (T4/T5), remove from `SU_DEV_FEATURES` (T4/T5), remove from `CPU_TEMPLATE_FEATURES` (T6), `test_template_post_create_enables_detached_environments` repoint (T6). ✅
- README / CHANGELOG / RELEASE.md → Task 7. ✅
- `dvt` needs no change (dynamic registry) → noted in spec non-goals; nothing to do. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step has literal file content. ✅

**Type/name consistency:**
- `_dockerfile_stage` (T1) — used only in T1. ✅
- `BREW_FEATURES` introduced T4 as `["rust-devtools"]`, extended T5 to `["rust-devtools", "cpp-devtools"]`; `test_brew_calls_run_as_dev` defined once (T4). ✅
- `SLIM_TEMPLATE_FEATURES` / `PIXI_TEMPLATE_FEATURES` defined T6, used same task. ✅
- `SU_DEV_FEATURES` edited T4 then T5 — T5's "before" block matches T4's "after" exactly. ✅
- Image ref string `ghcr.io/jesserobertson/base-ubuntu-slim:latest` identical in T2 (json + test), T3 (ps1 + yml), T6 (templates + tests), T7 (README). ✅
- Feature version `1.1.0` consistent across T4/T5 install/json and T7 CHANGELOG. ✅
- Dockerfile `full` stage string `'ENV PIXI_HOME="/home/dev/.local/share/pixi"'` in T1 impl matches the T1 test assertion byte-for-byte. ✅
