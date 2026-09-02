# Shell & CLI Features + Base Image Assembly — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Homebrew, the interactive CLI bundle, and pixi from `base/Dockerfile`'s `full` stage into three published devcontainer features, assemble `base-ubuntu` / `base-cuda` from them via `devcontainer build`, and re-point every toolchain template onto the leaner `-slim` bases.

**Architecture:** `base/Dockerfile` keeps only `core` → `slim`, published as `base-ubuntu-slim` and a new `base-cuda-slim` via plain `docker build`. New `features/{homebrew,shell-kit,pixi}` carry what `full` used to bake in. `images/base-ubuntu/.devcontainer/devcontainer.json` and `images/base-cuda/.devcontainer/devcontainer.json` declare `-slim base + the three features`; `build.yml` and `build-images.ps1` gain a second phase that runs `devcontainer build --push` for those bundles. Downstream features declare `dependsOn` on `pixi` (Python toolchains) or `homebrew` (native toolchains) so they self-provision on a slim base; their templates re-point to `-slim`.

**Tech Stack:** devcontainer features spec (`devcontainer-feature.json`, `dependsOn`, `installsAfter`, `containerEnv`), `@devcontainers/cli` (`devcontainer build`), Docker multi-stage builds, GitHub Actions, PowerShell (`build-images.ps1` + Pester tests), pytest (`tests/test_static.py`, `tests/features/`, `tests/integration/`).

**Spec:** `docs/superpowers/specs/2026-09-02-shell-cli-features-design.md`

## Global Constraints

- Feature publish namespace is `ghcr.io/jesserobertson/devcontainers/<id>` (set by `devcontainers/action` with `base-path-to-features: features`, repo `jesserobertson/devcontainers`). Every feature ref — in `dependsOn`, `installsAfter`, `images/*/​.devcontainer/devcontainer.json`, and templates — uses this exact prefix.
- The non-root user is `dev`, home `/home/dev`, no passwordless sudo. Homebrew refuses to run as root: every `brew`/`pixi` invocation in an `install.sh` runs via `su dev -c`.
- Homebrew prefix is the hardcoded `/home/linuxbrew/.linuxbrew`. pixi lives at `PIXI_HOME=/home/dev/.local/share/pixi`, binary at `/home/dev/.local/share/pixi/bin/pixi`.
- New features start at `version` `1.0.0`. An edited feature's `version` gets its **minor** component bumped from whatever is currently published (values in this plan assume: `cli` 1.1.0, `fastapi` 1.1.0, `huggingface` 1.1.0, `jax` 1.1.0, `marimo` 1.1.0, `mojo` 1.1.0, `ollama` 1.0.0, `py-devtools` 1.2.0, `pytorch` 1.1.0, `rapids` 1.1.0, `transformers` 1.1.0, `rust-devtools` 1.1.0, `cpp-devtools` 1.1.0).
- `dependsOn` value form in `devcontainer-feature.json` is an object: `{ "<full-ref>": {} }`. `installsAfter` is an array of bare refs.
- All template `devcontainer.json` files keep `remoteUser: "dev"`, `workspaceFolder: "/workspace"`, their existing `workspaceMount`, and their existing `postCreateCommand` (which sets `detached-environments = true` then runs `pixi install` for the pixi templates). Only the `image` field changes for a re-pointed template.
- `base/Dockerfile` must still end with a line that is exactly `USER dev` and must never contain `NOPASSWD`.
- Commit after every green step. Never use `git add -A`; stage explicit paths.

---

## File Structure

**Created:**
- `features/homebrew/devcontainer-feature.json`, `features/homebrew/install.sh`
- `features/shell-kit/devcontainer-feature.json`, `features/shell-kit/install.sh`
- `features/pixi/devcontainer-feature.json`, `features/pixi/install.sh`
- `images/base-ubuntu/.devcontainer/devcontainer.json`
- `images/base-cuda/.devcontainer/devcontainer.json`
- `images/base-cuda-slim.json`
- `tests/features/test_homebrew.py`, `tests/features/test_shell_kit.py`, `tests/features/test_pixi.py`
- `tests/integration/test_base_assembly.py`

**Modified:**
- `base/Dockerfile` — delete `full` stage; remove the Homebrew install block from `core`
- `images/base-ubuntu.json`, `images/base-cuda.json` — description text
- `.github/workflows/build.yml` — split into `build-slim` + `build-bundles`
- `build-images.ps1`, `build-images.tests.ps1` — two-builder logic
- `features/{cli,fastapi,huggingface,jax,marimo,mojo,ollama,py-devtools,pytorch,rapids,transformers}/devcontainer-feature.json` — add `dependsOn: pixi`, bump version
- `features/{rust-devtools,cpp-devtools}/devcontainer-feature.json` — add `dependsOn: homebrew`, bump version
- `templates/{cli,fastapi,marimo,huggingface,ollama,py-devtools}/devcontainer.json` — `image` → `base-ubuntu-slim`
- `templates/{jax,pytorch,rapids,transformers,mojo}/devcontainer.json` — `image` → `base-cuda-slim`
- `tests/test_static.py` — Dockerfile stage assertions, template base-image groups, `FEATURES`/`IMAGES` lists, new feature checks
- `README.md`, `CHANGELOG.md`, `RELEASE.md` — document the restructure

---

## Task 1: `features/homebrew`

**Files:**
- Create: `features/homebrew/devcontainer-feature.json`
- Create: `features/homebrew/install.sh`
- Create: `tests/features/test_homebrew.py`
- Modify: `tests/test_static.py:14-18` (add `"homebrew"` to `FEATURES`)

**Interfaces:**
- Produces: feature published as `ghcr.io/jesserobertson/devcontainers/homebrew:latest`. `containerEnv` keys `PATH`, `HOMEBREW_NO_AUTO_UPDATE`, `HOMEBREW_NO_ANALYTICS`. No options. `install.sh` is idempotent (no-op when `/home/linuxbrew/.linuxbrew/bin/brew` is executable).
- Consumed by: Task 3 (`shell-kit` `dependsOn`), Task 5 (bundle configs), Task 8 (`rust-devtools`/`cpp-devtools` `dependsOn`).

- [ ] **Step 1: Add `homebrew` to the static `FEATURES` list**

In `tests/test_static.py`, change the `FEATURES` list (line 14) to include `"homebrew"`:

```python
FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
    "agent", "podman", "rust-devtools", "cpp-devtools",
    "homebrew", "pixi", "shell-kit",
]
```

(All three new names are added now; Tasks 2 and 3 create the other two. `test_published_feature_version_matches_local_content` tolerates an unpublished version by returning early, so a missing GHCR artifact for the new names passes.)

- [ ] **Step 2: Write the failing feature test**

Create `tests/features/test_homebrew.py`:

```python
"""Static checks for the homebrew feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "homebrew"


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_no_options():
    data = _json()
    assert data["id"] == "homebrew"
    assert data.get("options", {}) == {}


def test_container_env_puts_brew_on_path_and_silences_it():
    env = _json()["containerEnv"]
    assert env["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert env["HOMEBREW_NO_ANALYTICS"] == "1"
    assert "/home/linuxbrew/.linuxbrew/bin" in env["PATH"]
    assert env["PATH"].endswith(":${PATH}")


def test_install_sh_is_idempotent_and_installs_as_dev():
    script = (FEATURE_DIR / "install.sh").read_text()
    assert "if [ -x /home/linuxbrew/.linuxbrew/bin/brew ]" in script
    assert "chown dev:dev /home/linuxbrew" in script
    assert 'su dev -c ' in script
    installer_lines = [l for l in script.splitlines() if "install.sh" in l and "curl" in l]
    assert installer_lines and all("su dev -c" in l for l in installer_lines)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pixi run pytest tests/features/test_homebrew.py -v` (from repo root; if the repo root has no pixi env, use `python -m pytest`)
Expected: FAIL — `FileNotFoundError` on the missing `devcontainer-feature.json`.

- [ ] **Step 4: Create `features/homebrew/devcontainer-feature.json`**

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

- [ ] **Step 5: Create `features/homebrew/install.sh`**

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

- [ ] **Step 6: Run the full static + feature suite for this feature**

Run: `pixi run pytest tests/features/test_homebrew.py tests/test_static.py -k "homebrew" -v`
Expected: PASS — including the parametrized `test_feature_json_has_required_fields[homebrew]`, `test_feature_json_id_matches_dir[homebrew]`, `test_install_sh_syntax[homebrew]`.

- [ ] **Step 7: Commit**

```bash
git add features/homebrew/ tests/features/test_homebrew.py tests/test_static.py
git commit -m "feat(homebrew): add Homebrew package-manager feature

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 2: `features/pixi`

**Files:**
- Create: `features/pixi/devcontainer-feature.json`
- Create: `features/pixi/install.sh`
- Create: `tests/features/test_pixi.py`

**Interfaces:**
- Consumes: nothing at build time (works standalone). `installsAfter` `homebrew`, `shell-kit` (Task 1, Task 3) for ordering only.
- Produces: feature `ghcr.io/jesserobertson/devcontainers/pixi:latest`. Options `shellHook` (enum `auto`/`off`, default `auto`), `global` (string, default `""`). `containerEnv` keys `PIXI_HOME`, `PATH`, `UV_HTTP_TIMEOUT`. `install.sh` writes the `~/.bashrc` pixi shell-hook line always (under `shellHook=auto`) and `~/.config/fish/conf.d/project-pixi.fish` only when `/home/linuxbrew/.linuxbrew/bin/fish` is executable.
- Consumed by: Task 5 (bundle configs), Task 9 (`dependsOn` from Python toolchains).

- [ ] **Step 1: Write the failing feature test**

Create `tests/features/test_pixi.py`:

```python
"""Static checks for the pixi feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "pixi"


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_options():
    data = _json()
    assert data["id"] == "pixi"
    opts = data["options"]
    assert opts["shellHook"]["default"] == "auto"
    assert set(opts["shellHook"]["enum"]) == {"auto", "off"}
    assert opts["global"]["default"] == ""


def test_container_env():
    env = _json()["containerEnv"]
    assert env["PIXI_HOME"] == "/home/dev/.local/share/pixi"
    assert env["PATH"] == "/home/dev/.local/share/pixi/bin:${PATH}"
    assert env["UV_HTTP_TIMEOUT"] == "300"


def test_installs_after_homebrew_and_shell_kit():
    after = _json()["installsAfter"]
    assert "ghcr.io/jesserobertson/devcontainers/homebrew" in after
    assert "ghcr.io/jesserobertson/devcontainers/shell-kit" in after


def test_no_depends_on():
    # pixi works standalone; ordering is installsAfter only.
    assert "dependsOn" not in _json()


def test_install_sh_guards_fish_snippet_and_runs_global_as_dev():
    script = (FEATURE_DIR / "install.sh").read_text()
    assert 'export PIXI_HOME="/home/dev/.local/share/pixi"' in script
    assert 'if [ "${SHELLHOOK:-auto}" = "auto" ]' in script
    assert "if [ -x /home/linuxbrew/.linuxbrew/bin/fish ]" in script
    assert 'if [ -n "${GLOBAL:-}" ]' in script
    assert "pixi global install" in script
    for line in script.splitlines():
        if "pixi global install" in line:
            assert "su dev -c" in line
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pixi run pytest tests/features/test_pixi.py -v`
Expected: FAIL — missing `devcontainer-feature.json`.

- [ ] **Step 3: Create `features/pixi/devcontainer-feature.json`**

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

- [ ] **Step 4: Create `features/pixi/install.sh`**

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

- [ ] **Step 5: Run the suite**

Run: `pixi run pytest tests/features/test_pixi.py tests/test_static.py -k "pixi" -v`
Expected: PASS — including `test_install_sh_syntax[pixi]` (`bash -n`) and `test_pixi_calls_run_as_dev[...]` is unaffected (that list is `SU_DEV_FEATURES`, which does not include `pixi`).

- [ ] **Step 6: Commit**

```bash
git add features/pixi/ tests/features/test_pixi.py
git commit -m "feat(pixi): add pixi feature with shell-hook and global-install options

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 3: `features/shell-kit`

**Files:**
- Create: `features/shell-kit/devcontainer-feature.json`
- Create: `features/shell-kit/install.sh`
- Create: `tests/features/test_shell_kit.py`

**Interfaces:**
- Consumes: `homebrew` (Task 1) via `dependsOn` and `installsAfter`.
- Produces: feature `ghcr.io/jesserobertson/devcontainers/shell-kit:latest`. Option `loginShell` (boolean, default `true`). `containerEnv` key `SHELL=/home/linuxbrew/.linuxbrew/bin/fish`. `install.sh` brew-installs the 11-formula bundle + `bat-extras` and `chsh`es `dev` to fish unless `LOGINSHELL=false`.

- [ ] **Step 1: Write the failing feature test**

Create `tests/features/test_shell_kit.py`:

```python
"""Static checks for the shell-kit feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "shell-kit"

BUNDLE = ["bat", "bat-extras", "eza", "fd", "fish", "fzf", "jq", "just",
         "neovim", "ripgrep", "starship", "zoxide"]


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_login_shell_option():
    data = _json()
    assert data["id"] == "shell-kit"
    assert data["options"]["loginShell"]["type"] == "boolean"
    assert data["options"]["loginShell"]["default"] is True


def test_depends_on_and_installs_after_homebrew():
    data = _json()
    assert data["dependsOn"] == {"ghcr.io/jesserobertson/devcontainers/homebrew": {}}
    assert "ghcr.io/jesserobertson/devcontainers/homebrew" in data["installsAfter"]


def test_container_env_shell_is_fish():
    assert _json()["containerEnv"]["SHELL"] == "/home/linuxbrew/.linuxbrew/bin/fish"


def test_install_sh_installs_full_bundle_as_dev_and_conditionally_chsh():
    script = (FEATURE_DIR / "install.sh").read_text()
    brew_line = next(l for l in script.splitlines() if "brew install" in l)
    assert "su dev -c" in brew_line or "su dev -c" in script.split("brew install")[0].splitlines()[-1]
    for pkg in BUNDLE:
        assert pkg in script, f"{pkg} missing from bundle"
    assert 'if [ "${LOGINSHELL:-true}" = "true" ]' in script
    assert "chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev" in script
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pixi run pytest tests/features/test_shell_kit.py -v`
Expected: FAIL — missing `devcontainer-feature.json`.

- [ ] **Step 3: Create `features/shell-kit/devcontainer-feature.json`**

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

- [ ] **Step 4: Create `features/shell-kit/install.sh`**

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

- [ ] **Step 5: Run the suite**

Run: `pixi run pytest tests/features/test_shell_kit.py tests/test_static.py -k "shell_kit or shell-kit" -v`
Expected: PASS — including `test_install_sh_syntax[shell-kit]`.

- [ ] **Step 6: Commit**

```bash
git add features/shell-kit/ tests/features/test_shell_kit.py
git commit -m "feat(shell-kit): add interactive fish + CLI bundle feature

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 4: Strip `base/Dockerfile` to `core` → `slim`

**Files:**
- Modify: `base/Dockerfile` — delete the `full` stage; remove the Homebrew install block from `core`
- Modify: `tests/test_static.py` — rewrite the Dockerfile stage assertions (lines ~375-421)

**Interfaces:**
- Produces: `docker build --target slim base/` → an image with the apt kit, the `dev` user, chezmoi dotfiles, and **no** Homebrew, pixi, CLI bundle, or fish. `slim` is `FROM core` with nothing added. No stage named `full` exists.

- [ ] **Step 1: Rewrite the Dockerfile stage tests to expect core/slim only**

In `tests/test_static.py`, replace `test_dockerfile_has_core_slim_full_stages`, `test_dockerfile_core_stage_is_pixi_free`, `test_dockerfile_slim_stage_adds_nothing`, `test_dockerfile_full_stage_has_pixi_and_cli_bundle`, and `test_dockerfile_sets_pixi_home` with:

```python
def test_dockerfile_has_core_and_slim_stages_only():
    text = _dockerfile_text()
    assert re.search(r"^FROM \S+ AS core$", text, re.M)
    assert re.search(r"^FROM core AS slim$", text, re.M)
    assert not re.search(r"^FROM \S+ AS full$", text, re.M), "the full stage must be gone"


def test_dockerfile_core_stage_has_no_pixi_no_brew_no_cli_bundle():
    core = _dockerfile_stage("core")
    assert "pixi.sh/install.sh" not in core
    assert "PIXI_HOME" not in core
    assert "brew install" not in core
    assert "Homebrew/install/HEAD/install.sh" not in core


def test_dockerfile_slim_stage_adds_nothing():
    assert "RUN" not in _dockerfile_stage("slim")


def test_dockerfile_does_not_set_pixi_home():
    # PIXI_HOME now lives only in the pixi feature's containerEnv.
    assert "PIXI_HOME" not in _dockerfile_text()
```

- [ ] **Step 2: Run to verify the new tests fail against today's Dockerfile**

Run: `pixi run pytest tests/test_static.py -k "dockerfile" -v`
Expected: FAIL — `test_dockerfile_has_core_and_slim_stages_only` (a `full` stage still exists), `test_dockerfile_core_stage_has_no_pixi_no_brew_no_cli_bundle` (core still installs Homebrew), `test_dockerfile_does_not_set_pixi_home`.

- [ ] **Step 3: Edit `base/Dockerfile`**

Delete everything from `# ────… full …` / `FROM core AS full` to end of file. In the `core` stage, delete these three lines and their preceding comment block (the paragraph starting "Homebrew's Linux installer always installs to the hardcoded prefix"):

```dockerfile
RUN mkdir -p /home/linuxbrew && chown dev:dev /home/linuxbrew
RUN su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
```

Keep the `ENV PATH=…`, `ENV HOMEBREW_NO_AUTO_UPDATE=1`, `ENV HOMEBREW_NO_ANALYTICS=1`, `ENV UV_HTTP_TIMEOUT=300` lines (harmless with no brew; the features re-assert what they own). Keep the chezmoi lines, the `git config … safe.directory`, `chown -R dev:dev /home/dev`, `WORKDIR /workspace`, `USER dev`. The file must still end with the `slim` stage:

```dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# slim — core with nothing added. base-ubuntu-slim / base-cuda-slim publish this.
# base-ubuntu / base-cuda are assembled from this image + the homebrew,
# shell-kit and pixi features via `devcontainer build` (see images/).
# ─────────────────────────────────────────────────────────────────────────────
FROM core AS slim
```

Ensure the last non-blank line of the file is `USER dev` (it is, in `core`, which `slim` inherits — but `test_dockerfile_ends_as_dev_user` checks the literal last non-blank line; if `FROM core AS slim` is now last, that test breaks). To keep `test_dockerfile_ends_as_dev_user` passing, add a trailing `USER dev` to the `slim` stage:

```dockerfile
FROM core AS slim
USER dev
```

- [ ] **Step 4: Run the Dockerfile tests**

Run: `pixi run pytest tests/test_static.py -k "dockerfile" -v`
Expected: PASS — all of them, including the unchanged `test_dockerfile_creates_dev_user`, `test_dockerfile_no_passwordless_sudo`, `test_dockerfile_ends_as_dev_user`.

- [ ] **Step 5: Sanity-build the slim image locally (optional but recommended)**

Run: `docker build --target slim -t base-slim-check base/`
Then: `docker run --rm base-slim-check bash -lc 'command -v brew; command -v pixi; command -v fish; echo done'`
Expected: three empty lines then `done` — none of the three are present; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add base/Dockerfile tests/test_static.py
git commit -m "refactor(base): drop the full stage; core/slim only

Homebrew, the CLI bundle and pixi now live in the homebrew, shell-kit and
pixi features. base-ubuntu / base-cuda are assembled from slim + those
features via devcontainer build.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 5: Bundle build-configs + `base-cuda-slim` registry entry

**Files:**
- Create: `images/base-ubuntu/.devcontainer/devcontainer.json`
- Create: `images/base-cuda/.devcontainer/devcontainer.json`
- Create: `images/base-cuda-slim.json`
- Modify: `images/base-ubuntu.json`, `images/base-cuda.json` — description
- Modify: `tests/test_static.py` — `IMAGES` list (line ~535), add bundle-config checks

**Interfaces:**
- Produces: `images/base-ubuntu/.devcontainer/devcontainer.json` with `image: ghcr.io/jesserobertson/base-ubuntu-slim:latest` and `features` = exactly `{homebrew, shell-kit, pixi}` at `:latest`. `images/base-cuda/.devcontainer/devcontainer.json` identical but `image: …/base-cuda-slim:latest`. `images/base-cuda-slim.json` is a `dvt` registry entry with `name` `base-cuda-slim`, `ref` `ghcr.io/jesserobertson/base-cuda-slim:latest`, `aliases` `["cuda-slim", "gpu-slim"]`.
- Consumed by: Task 6 (`build.yml build-bundles`), Task 7 (`build-images.ps1`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_static.py`, change `IMAGES` (line ~535) to:

```python
IMAGES = ["base-ubuntu", "base-cuda", "base-ubuntu-slim", "base-cuda-slim"]
```

Add near the other `images/` tests:

```python
def test_base_cuda_slim_ref():
    assert (
        _image_json("base-cuda-slim")["ref"]
        == "ghcr.io/jesserobertson/base-cuda-slim:latest"
    )


BUNDLE_CONFIGS = {
    "base-ubuntu": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
    "base-cuda": "ghcr.io/jesserobertson/base-cuda-slim:latest",
}
PLUMBING_FEATURE_REFS = {
    "ghcr.io/jesserobertson/devcontainers/homebrew:latest",
    "ghcr.io/jesserobertson/devcontainers/shell-kit:latest",
    "ghcr.io/jesserobertson/devcontainers/pixi:latest",
}


@pytest.mark.parametrize("bundle,base_ref", BUNDLE_CONFIGS.items())
def test_bundle_config_composes_slim_plus_three_features(bundle, base_ref):
    cfg = _devcontainer_json(f"images/{bundle}/.devcontainer/devcontainer.json")
    assert cfg["image"] == base_ref
    assert set(cfg["features"]) == PLUMBING_FEATURE_REFS
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -k "bundle_config or base_cuda_slim or image_json" -v`
Expected: FAIL — missing `images/base-cuda-slim.json` and the two bundle configs.

- [ ] **Step 3: Create `images/base-ubuntu/.devcontainer/devcontainer.json`**

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

- [ ] **Step 4: Create `images/base-cuda/.devcontainer/devcontainer.json`**

```json
{
  "name": "base-cuda",
  "image": "ghcr.io/jesserobertson/base-cuda-slim:latest",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/homebrew:latest": {},
    "ghcr.io/jesserobertson/devcontainers/shell-kit:latest": {},
    "ghcr.io/jesserobertson/devcontainers/pixi:latest": {}
  }
}
```

- [ ] **Step 5: Create `images/base-cuda-slim.json`**

```json
{
  "name": "base-cuda-slim",
  "description": "Bare CUDA 12.8 devcontainer core - apt kit, the dev user, and the chezmoi dotfiles, with no Homebrew, no pixi and no CLI bundle. The lean base GPU toolchain features compose onto.",
  "ref": "ghcr.io/jesserobertson/base-cuda-slim:latest",
  "aliases": ["cuda-slim", "gpu-slim"]
}
```

- [ ] **Step 6: Update `images/base-ubuntu.json` and `images/base-cuda.json` descriptions**

`images/base-ubuntu.json` `description`:
> `"Ubuntu 24.04 devcontainer base = base-ubuntu-slim + the homebrew, shell-kit and pixi features (fish, Homebrew CLI bundle, pixi with project shell-hooks). Assembled via devcontainer build."`

`images/base-cuda.json` `description`:
> `"CUDA 12.8 devcontainer base = base-cuda-slim + the homebrew, shell-kit and pixi features. Assembled via devcontainer build."`

Leave `name`, `ref`, `aliases` untouched.

- [ ] **Step 7: Run the images test slice + full static suite**

Run: `pixi run pytest tests/test_static.py -v`
Expected: PASS. (`test_image_json_has_required_fields[base-cuda-slim]` and `test_image_json_name_matches_filename[base-cuda-slim]` now cover the new file.)

- [ ] **Step 8: Commit**

```bash
git add images/
git commit -m "feat(images): add bundle build-configs and base-cuda-slim registry entry

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 6: `.github/workflows/build.yml` — two-phase build

**Files:**
- Modify: `.github/workflows/build.yml`

**Interfaces:**
- Produces: job `build-slim` (matrix `base-ubuntu-slim`, `base-cuda-slim`; `docker/build-push-action` `target: slim`); job `build-bundles` (`needs: build-slim`; matrix `base-ubuntu`, `base-cuda`; runs `devcontainer build --workspace-folder images/<name> --push true --image-name <tag>`). Trigger `paths` include `images/**` and `features/{homebrew,shell-kit,pixi}/**`.

- [ ] **Step 1: Rewrite `build.yml`**

Replace the file with:

```yaml
name: Build and push base images

on:
  push:
    branches: [main]
    paths:
      - base/Dockerfile
      - images/**
      - features/homebrew/**
      - features/shell-kit/**
      - features/pixi/**
      - .github/workflows/build.yml
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  OWNER: jesserobertson

jobs:
  build-slim:
    name: ${{ matrix.name }}
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        include:
          - name: base-ubuntu-slim
            base_image: ubuntu:24.04
            tags: ghcr.io/jesserobertson/base-ubuntu-slim:latest
          - name: base-cuda-slim
            base_image: nvidia/cuda:12.8.0-devel-ubuntu24.04
            tags: ghcr.io/jesserobertson/base-cuda-slim:latest
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - uses: docker/login-action@v4
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v7
        with:
          context: base
          target: slim
          push: true
          build-args: BASE_IMAGE=${{ matrix.base_image }}
          tags: ${{ matrix.tags }}
          cache-from: type=gha,scope=${{ matrix.name }}
          cache-to: type=gha,mode=max,scope=${{ matrix.name }}

  build-bundles:
    name: ${{ matrix.name }}
    runs-on: ubuntu-latest
    needs: build-slim
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        include:
          - name: base-ubuntu
            tags: |
              ghcr.io/jesserobertson/base-ubuntu:latest
          - name: base-cuda
            tags: |
              ghcr.io/jesserobertson/base-cuda:latest
              ghcr.io/jesserobertson/base-cuda:cuda12.8.0
    steps:
      - uses: actions/checkout@v7
      - uses: docker/login-action@v4
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - run: npm install -g @devcontainers/cli
      - name: devcontainer build ${{ matrix.name }}
        run: |
          image_args=()
          while IFS= read -r tag; do
            [ -n "$tag" ] && image_args+=(--image-name "$tag")
          done <<< "${{ matrix.tags }}"
          devcontainer build \
            --workspace-folder "images/${{ matrix.name }}" \
            --push true \
            "${image_args[@]}"
```

- [ ] **Step 2: Validate the YAML parses**

Run: `pixi run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Add a static check for the two-phase structure**

In `tests/test_static.py`, add:

```python
def test_build_yml_has_slim_and_bundle_jobs():
    data = _yaml(".github/workflows/build.yml")
    jobs = data["jobs"]
    assert set(jobs) == {"build-slim", "build-bundles"}
    assert jobs["build-bundles"]["needs"] == "build-slim"
    slim_names = {m["name"] for m in jobs["build-slim"]["strategy"]["matrix"]["include"]}
    assert slim_names == {"base-ubuntu-slim", "base-cuda-slim"}
    bundle_names = {m["name"] for m in jobs["build-bundles"]["strategy"]["matrix"]["include"]}
    assert bundle_names == {"base-ubuntu", "base-cuda"}
```

- [ ] **Step 4: Run**

Run: `pixi run pytest tests/test_static.py -k "build_yml" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build.yml tests/test_static.py
git commit -m "ci(build): split into build-slim (docker) and build-bundles (devcontainer build)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 7: `build-images.ps1` — two builders

**Files:**
- Modify: `build-images.ps1`
- Modify: `build-images.tests.ps1`

**Interfaces:**
- Produces: `$ImageDefs` entries for `base-ubuntu` / `base-cuda` carry `Builder = 'devcontainer'` and `ConfigDir = 'images/<name>'` instead of `Context`/`Target`; `base-ubuntu-slim` / `base-cuda-slim` carry `Context = 'base'`, `Target = 'slim'`. New `Invoke-DevcontainerBuild` helper. `-Images` ValidateSet includes `base-cuda-slim`; selecting a bundle auto-adds its `-slim`. `-Features` list includes `homebrew`, `pixi`, `shell-kit`.

- [ ] **Step 1: Update the Pester expectations first**

In `build-images.tests.ps1`, adjust/add (matching existing test style in that file):
- The `base-ubuntu-slim` build calls `docker` with `build`, `--target`, `slim`, context `base`.
- Add the same for `base-cuda-slim`.
- `base-ubuntu` / `base-cuda` no longer call `docker build`; they call `devcontainer` with `build`, `--workspace-folder`, `images/base-ubuntu` (resp. `images/base-cuda`), `--image-name` once per tag.
- Selecting `-Images base-ubuntu` (without `base-ubuntu-slim`) emits a warning and prepends `base-ubuntu-slim`; same for `base-cuda` → `base-cuda-slim`.
- The default `$Features` array contains `homebrew`, `pixi`, `shell-kit`.

Run: `pwsh -Command "Invoke-Pester ./build-images.tests.ps1"` — expect FAIL on the new cases.

- [ ] **Step 2: Edit `build-images.ps1` — param sets**

- `[ValidateSet('base-ubuntu', 'base-ubuntu-slim', 'base-cuda', 'base-cuda-slim', 'ramalama')]` and add `base-cuda-slim` to the default `$Images` array.
- `[ValidateSet(...)]` for `$Features` and the default array: add `'homebrew'`, `'pixi'`, `'shell-kit'` (keep alphabetical-ish with the rest).

- [ ] **Step 3: Edit `$ImageDefs`**

```powershell
$ImageDefs = [ordered]@{
    'base-ubuntu' = @{
        Builder   = 'devcontainer'
        ConfigDir = 'images/base-ubuntu'
        Tags      = @("$Registry/$Owner/base-ubuntu:latest")
    }
    'base-ubuntu-slim' = @{
        Context   = 'base'
        Target    = 'slim'
        BuildArgs = @{ BASE_IMAGE = 'ubuntu:24.04' }
        Tags      = @("$Registry/$Owner/base-ubuntu-slim:latest")
    }
    'base-cuda' = @{
        Builder   = 'devcontainer'
        ConfigDir = 'images/base-cuda'
        Tags      = @(
            "$Registry/$Owner/base-cuda:latest",
            "$Registry/$Owner/base-cuda:cuda12.8.0"
        )
    }
    'base-cuda-slim' = @{
        Context   = 'base'
        Target    = 'slim'
        BuildArgs = @{ BASE_IMAGE = 'nvidia/cuda:12.8.0-devel-ubuntu24.04' }
        Tags      = @("$Registry/$Owner/base-cuda-slim:latest")
    }
    'ramalama' = @{
        Context   = 'ramalama'
        BuildArgs = @{}
        Tags      = @("$Registry/$Owner/ramalama:latest")
    }
}
```

- [ ] **Step 4: Add the bundle→slim auto-add nudge**

Right after the existing `ramalama`→`base-cuda` block:

```powershell
$bundleToSlim = @{ 'base-ubuntu' = 'base-ubuntu-slim'; 'base-cuda' = 'base-cuda-slim' }
foreach ($bundle in $bundleToSlim.Keys) {
    $slim = $bundleToSlim[$bundle]
    if ($bundle -in $Images -and $slim -notin $Images) {
        Write-Warning "$bundle is assembled from $slim - adding $slim to the image list."
        $Images = @($slim) + $Images
    }
}
```

- [ ] **Step 5: Add `Invoke-DevcontainerBuild` and dispatch on `Builder`**

Add near `Invoke-Build`:

```powershell
function Invoke-DevcontainerBuild {
    [CmdletBinding()]
    param([string]$ConfigDir, [string[]]$Tags, [switch]$Push)

    if (-not (Get-Command devcontainer -ErrorAction SilentlyContinue)) {
        throw "devcontainer CLI not found. Install it with: npm install -g @devcontainers/cli"
    }
    $args = @('build', '--workspace-folder', $ConfigDir)
    foreach ($tag in $Tags) { $args += '--image-name', $tag }
    if ($Push) { $args += '--push', 'true' }
    Write-Host "    devcontainer $($args -join ' ')"
    devcontainer @args
    if ($LASTEXITCODE -ne 0) { throw "devcontainer build failed for $ConfigDir" }
}
```

In the per-image loop's local-build branch, dispatch:

```powershell
if ($def.Builder -eq 'devcontainer') {
    Invoke-DevcontainerBuild -ConfigDir $def.ConfigDir -Tags $def.Tags
} else {
    # existing docker build path, unchanged
}
```

(Local runs omit `-Push`; `devcontainer build` loads the image into the local docker store by default. The `-Pull` branch is untouched — it pulls finished images from GHCR regardless of builder.)

- [ ] **Step 6: Run Pester**

Run: `pwsh -Command "Invoke-Pester ./build-images.tests.ps1"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add build-images.ps1 build-images.tests.ps1
git commit -m "build(images): drive base-ubuntu/base-cuda via devcontainer build

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 8: `dependsOn: homebrew` on the native-toolchain features

**Files:**
- Modify: `features/rust-devtools/devcontainer-feature.json`
- Modify: `features/cpp-devtools/devcontainer-feature.json`
- Modify: `tests/test_static.py` — add a `dependsOn` check for `BREW_FEATURES`

**Interfaces:**
- Produces: both features gain `"dependsOn": { "ghcr.io/jesserobertson/devcontainers/homebrew": {} }` and `version` `1.2.0`. This is load-bearing — `base-ubuntu-slim` no longer ships brew, so without it a fresh `rust-devtools`/`cpp-devtools` container fails at `brew: command not found`.

- [ ] **Step 1: Write the failing test**

In `tests/test_static.py`, add:

```python
@pytest.mark.parametrize("feature", BREW_FEATURES)
def test_brew_feature_depends_on_homebrew(feature):
    data = _feature_json(feature)
    assert data["dependsOn"] == {"ghcr.io/jesserobertson/devcontainers/homebrew": {}}
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -k "depends_on_homebrew" -v`
Expected: FAIL — `KeyError: 'dependsOn'`.

- [ ] **Step 3: Edit both feature JSONs**

To each of `features/rust-devtools/devcontainer-feature.json` and `features/cpp-devtools/devcontainer-feature.json`:
- add top-level key `"dependsOn": { "ghcr.io/jesserobertson/devcontainers/homebrew": {} }`
- set `"version": "1.2.0"`

- [ ] **Step 4: Run**

Run: `pixi run pytest tests/test_static.py -k "rust-devtools or cpp-devtools or depends_on_homebrew" -v`
Expected: PASS. `test_published_feature_version_matches_local_content[rust-devtools]` and `[cpp-devtools]` pass because `1.2.0` is unpublished (returns early).

- [ ] **Step 5: Commit**

```bash
git add features/rust-devtools/ features/cpp-devtools/ tests/test_static.py
git commit -m "feat(rust-devtools,cpp-devtools): dependsOn homebrew (slim no longer bundles brew)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 9: `dependsOn: pixi` on the Python-toolchain features

**Files:**
- Modify: `features/{cli,fastapi,huggingface,jax,marimo,mojo,ollama,py-devtools,pytorch,rapids,transformers}/devcontainer-feature.json`
- Modify: `tests/test_static.py` — add a `PIXI_DEPENDENT_FEATURES` list + check

**Interfaces:**
- Produces: each of the 11 features gains `"dependsOn": { "ghcr.io/jesserobertson/devcontainers/pixi": {} }` and a minor `version` bump: `cli` 1.2.0, `fastapi` 1.2.0, `huggingface` 1.2.0, `jax` 1.2.0, `marimo` 1.2.0, `mojo` 1.2.0, `ollama` 1.1.0, `py-devtools` 1.3.0, `pytorch` 1.2.0, `rapids` 1.2.0, `transformers` 1.2.0.

- [ ] **Step 1: Write the failing test**

In `tests/test_static.py`, add near `SU_DEV_FEATURES`:

```python
PIXI_DEPENDENT_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
]


@pytest.mark.parametrize("feature", PIXI_DEPENDENT_FEATURES)
def test_pixi_feature_depends_on_pixi(feature):
    data = _feature_json(feature)
    assert data["dependsOn"] == {"ghcr.io/jesserobertson/devcontainers/pixi": {}}
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -k "depends_on_pixi" -v`
Expected: FAIL for all 11 — `KeyError: 'dependsOn'`.

- [ ] **Step 3: Edit each feature JSON**

For every feature in `PIXI_DEPENDENT_FEATURES`, add top-level
`"dependsOn": { "ghcr.io/jesserobertson/devcontainers/pixi": {} }`
and set `version` per the Interfaces list above. Do them one at a time, running
`pixi run pytest tests/test_static.py -k "test_pixi_feature_depends_on_pixi[<name>]" -v`
after each to watch it flip to PASS.

- [ ] **Step 4: Run the full static suite**

Run: `pixi run pytest tests/test_static.py -v`
Expected: PASS. (Existing `test_feature_json_has_required_fields`, version-drift checks all still green.)

- [ ] **Step 5: Commit**

```bash
git add features/ tests/test_static.py
git commit -m "feat(features): Python toolchains dependsOn pixi

cli, fastapi, huggingface, jax, marimo, mojo, ollama, py-devtools, pytorch,
rapids, transformers now self-provision pixi when composed onto a slim base.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 10: Re-point templates onto the `-slim` bases

**Files:**
- Modify: `templates/{cli,fastapi,marimo,huggingface,ollama,py-devtools}/devcontainer.json` — `image` → `ghcr.io/jesserobertson/base-ubuntu-slim:latest`
- Modify: `templates/{jax,pytorch,rapids,transformers,mojo}/devcontainer.json` — `image` → `ghcr.io/jesserobertson/base-cuda-slim:latest`
- Modify: `tests/test_static.py` — restructure the template base-image groups (lines ~25-34, ~207-259)

**Interfaces:**
- Produces: Python toolchain templates on `base-ubuntu-slim`, GPU toolchain templates on `base-cuda-slim`. `agent` and `podman` templates unchanged (`base-ubuntu`). `rust-devtools` / `cpp-devtools` templates unchanged (`base-ubuntu-slim`). Only the `image` string changes; `features`, `mounts`, `postCreateCommand`, `remoteUser` are untouched.

- [ ] **Step 1: Rework the template-group fixtures and tests**

In `tests/test_static.py`:

Replace the group lists (lines ~25-30) with:

```python
GPU_TEMPLATE_FEATURES = ["rapids", "mojo", "jax", "pytorch", "transformers"]
# Python toolchains: moved onto the slim CPU base, pixi arrives via dependsOn.
CPU_SLIM_TEMPLATE_FEATURES = [
    "marimo", "fastapi", "cli", "py-devtools", "huggingface", "ollama",
]
# Stay on the batteries-included base-ubuntu bundle.
BUNDLE_TEMPLATE_FEATURES = ["podman"]
# Native toolchains: already on slim, now with dependsOn homebrew.
SLIM_TEMPLATE_FEATURES = ["rust-devtools", "cpp-devtools"]

PIXI_TEMPLATE_FEATURES = [
    f for f in FEATURES
    if f not in SLIM_TEMPLATE_FEATURES + ["homebrew", "pixi", "shell-kit", "agent"]
]
```

Note: `agent` keeps its own dedicated tests (unchanged) and is excluded from `PIXI_TEMPLATE_FEATURES` because its template's `postCreateCommand` is the firewall arm, not `pixi install` — verify against `templates/agent/devcontainer.json` and, if it *does* currently carry the detached-environments line, leave `agent` in the list. `homebrew`/`pixi`/`shell-kit` have no template at all.

Replace `test_gpu_template_uses_base_cuda`:

```python
@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_uses_base_cuda_slim(feature):
    assert (
        _template_json(feature)["image"]
        == "ghcr.io/jesserobertson/base-cuda-slim:latest"
    )
```

Replace `test_cpu_template_uses_base_ubuntu` (and its `CPU_TEMPLATE_FEATURES` parametrize) with two:

```python
@pytest.mark.parametrize("feature", CPU_SLIM_TEMPLATE_FEATURES)
def test_cpu_slim_template_uses_base_ubuntu_slim(feature):
    assert (
        _template_json(feature)["image"]
        == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"
    )


@pytest.mark.parametrize("feature", BUNDLE_TEMPLATE_FEATURES)
def test_bundle_template_uses_base_ubuntu(feature):
    assert _template_json(feature)["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"
```

Update the other `CPU_TEMPLATE_FEATURES`-parametrized tests (`..._references_own_feature`, `..._remote_user_dev`, `..._no_sshd_waitloop`) to iterate `CPU_SLIM_TEMPLATE_FEATURES + BUNDLE_TEMPLATE_FEATURES`.

- [ ] **Step 2: Run to verify failure**

Run: `pixi run pytest tests/test_static.py -k "template" -v`
Expected: FAIL — the re-pointed templates still say `base-ubuntu` / `base-cuda`.

- [ ] **Step 3: Edit the template files**

In each of `templates/{cli,fastapi,marimo,huggingface,ollama,py-devtools}/devcontainer.json` set
`"image": "ghcr.io/jesserobertson/base-ubuntu-slim:latest"`.
In each of `templates/{jax,pytorch,rapids,transformers,mojo}/devcontainer.json` set
`"image": "ghcr.io/jesserobertson/base-cuda-slim:latest"`.
Change nothing else.

- [ ] **Step 4: Run**

Run: `pixi run pytest tests/test_static.py -v`
Expected: PASS — including `test_template_post_create_enables_detached_environments` (still parametrized over `PIXI_TEMPLATE_FEATURES`, still finding the line in each re-pointed template's unchanged `postCreateCommand`).

- [ ] **Step 5: Commit**

```bash
git add templates/ tests/test_static.py
git commit -m "feat(templates): re-point Python and GPU toolchains onto the -slim bases

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 11: Integration tests — assemble and probe the bases

**Files:**
- Create: `tests/integration/test_base_assembly.py`

**Interfaces:**
- Consumes: `images/base-ubuntu/.devcontainer/devcontainer.json` (Task 5), the published (or locally packaged) features. Uses `@devcontainers/cli` + Docker.
- Produces: pytest module, `@pytest.mark.integration` (matching the repo's existing integration-test marking — check `tests/integration/` for the marker/conftest convention and follow it).

- [ ] **Step 1: Inspect the existing integration-test conventions**

Read `tests/integration/` (conftest, one existing test) to learn: the marker name, how Docker availability is gated/skipped, and any helper for running a container. Mirror that style exactly in the new file.

- [ ] **Step 2: Write the integration tests**

Create `tests/integration/test_base_assembly.py`. Use the repo's existing skip-if-no-docker gate. Tests:

```python
"""Assemble the bundle images from features and probe the result.

Requires Docker and @devcontainers/cli. Skipped when either is absent.
These build from images/base-ubuntu/.devcontainer/devcontainer.json, which
references ghcr.io/jesserobertson/devcontainers/{homebrew,shell-kit,pixi}:latest
- so they pass only once those features are published (CI post-merge) or when
run with a local feature override. See the module docstring in CI.
"""
```

- `test_base_ubuntu_assembly_has_fish_brew_pixi`: `devcontainer build --workspace-folder images/base-ubuntu --image-name base-ubuntu-itest`, then `devcontainer run-user-commands` / `docker run --rm base-ubuntu-itest bash -lc '...'` asserting `command -v fish`, `command -v brew`, `command -v pixi` all succeed as `dev`.
- `test_base_ubuntu_slim_has_none_of_them`: `docker build --target slim -t slim-itest base/`, then assert `command -v fish`, `brew`, `pixi` all fail.
- `test_pixi_feature_writes_bash_hook_not_fish_on_slim`: build `slim-itest` + apply only the `pixi` feature (a tiny throwaway `.devcontainer/devcontainer.json` in a `tmp_path`), assert `/home/dev/.bashrc` contains `pixi shell-hook` and `/home/dev/.config/fish/conf.d/project-pixi.fish` does **not** exist.
- `test_rust_devtools_on_slim_pulls_homebrew`: throwaway config = `base-ubuntu-slim` + `rust-devtools` feature; assert `command -v brew` and `command -v cargo` both succeed (proves `dependsOn: homebrew` fired).

Keep each test's build in a `tmp_path` workspace; tear down images in a fixture.

- [ ] **Step 3: Run locally if Docker + CLI available (else confirm skip)**

Run: `pixi run pytest tests/integration/test_base_assembly.py -v`
Expected: PASS, or SKIPPED with a clear reason (no Docker / no `devcontainer` / features not yet published). A skip is an acceptable local outcome; CI runs them for real post-merge.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_base_assembly.py
git commit -m "test(integration): assemble bundle images from features and probe them

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 12: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `RELEASE.md`

**Interfaces:** none (docs only). The `dvt`-facing dependency-graph docs (`dvt feature deps`, the "Pulls in" column) are Plan 2's Task, not this one.

- [ ] **Step 1: `README.md` — base images table**

Rewrite the "Base images" table to four rows and add a sentence that the two `-slim` images are the only `base/Dockerfile` output and `base-ubuntu` / `base-cuda` are assembled from `-slim + homebrew + shell-kit + pixi` via `devcontainer build`:

| Image | From | Use for |
|-------|------|---------|
| `…/base-ubuntu-slim:latest` | `ubuntu:24.04` | Lean CPU base — apt kit + dotfiles only. Compose features onto it. |
| `…/base-cuda-slim:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | Lean GPU base — same, on CUDA. |
| `…/base-ubuntu:latest` | `base-ubuntu-slim` + homebrew + shell-kit + pixi | Batteries-included CPU — fish, CLI kit, pixi |
| `…/base-cuda:latest` | `base-cuda-slim` + homebrew + shell-kit + pixi | Batteries-included GPU |

- [ ] **Step 2: `README.md` — features table + shell-kit tip**

Add rows for `homebrew`, `shell-kit`, `pixi` to the Features table. In the "Using features in a project" area, add: a `py-devtools` (etc.) template now sits on `base-ubuntu-slim` — `dvt feature add shell-kit` (which pulls `homebrew`) or `dvt init --image base-ubuntu` restores fish + the CLI bundle. Update the `rust-devtools` / `cpp-devtools` rows to note `dependsOn: homebrew`.

- [ ] **Step 3: `CHANGELOG.md` — new dated section**

Under a new `## 2026-09-02` heading:
- `base/Dockerfile` `full` stage dissolved into new `homebrew`, `shell-kit`, `pixi` features.
- `base-cuda-slim` published; `base-ubuntu` / `base-cuda` now assembled via `devcontainer build`.
- Python toolchain templates re-pointed to `base-ubuntu-slim`; GPU templates to `base-cuda-slim`.
- `rust-devtools` / `cpp-devtools` gain `dependsOn: homebrew` — **breaking** for anyone layering their own `brew install` directly on `base-ubuntu-slim` without the `homebrew` feature.
- All Python toolchain features gain `dependsOn: pixi`.

- [ ] **Step 4: `RELEASE.md` — base-images build notes**

In the base-images section, note the two-phase build: `docker build --target slim` for `base-ubuntu-slim` / `base-cuda-slim`, then `devcontainer build --push` for the bundles, and that the three plumbing features must be published (via `publish-features.yml`) before a bundle build picks up their latest content — a same-push change lands on the next `build.yml` run.

- [ ] **Step 5: Run the docs-related static checks + full suites**

Run: `pixi run pytest tests/test_static.py -k "readme" -v` then `pixi run pytest tests/ -v` (skips integration without Docker).
Expected: PASS — `test_readme_documents_base_ubuntu_slim` still green; add nothing that breaks `test_readme_no_root_remote_user` / `test_readme_documents_agent`.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md RELEASE.md
git commit -m "docs: base images assembled from homebrew/shell-kit/pixi features

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku"
```

---

## Task 13: Full-suite verification + feature release tags

**Files:** none (verification + tagging)

- [ ] **Step 1: Run the entire non-integration suite**

Run: `pixi run pytest tests/ -v --deselect tests/integration` (or the repo's usual `pixi run test` equivalent from the root `pyproject.toml`)
Expected: all PASS.

- [ ] **Step 2: Run Pester**

Run: `pwsh -Command "Invoke-Pester ./build-images.tests.ps1"`
Expected: all PASS.

- [ ] **Step 3: Lint YAML + JSON**

Run: `pixi run python -c "import json,glob; [json.load(open(f)) for f in glob.glob('features/**/devcontainer-feature.json', recursive=True) + glob.glob('images/**/*.json', recursive=True) + glob.glob('templates/**/devcontainer.json', recursive=True)]; print('json ok')"`
Expected: `json ok`.

- [ ] **Step 4: Confirm branch is clean and push**

```bash
git status            # clean
git log --oneline -14 # the task commits
git push -u origin HEAD
```

- [ ] **Step 5: After merge to `main`, cut the feature release tags**

Once CI's `publish-features.yml` has published the new/changed features, tag each so `release-features.yml` cuts a GitHub release (matches the repo's existing `feat-<name>-v<version>` convention):

```
feat-homebrew-v1.0.0
feat-shell-kit-v1.0.0
feat-pixi-v1.0.0
feat-rust-devtools-v1.2.0
feat-cpp-devtools-v1.2.0
feat-cli-v1.2.0
feat-fastapi-v1.2.0
feat-huggingface-v1.2.0
feat-jax-v1.2.0
feat-marimo-v1.2.0
feat-mojo-v1.2.0
feat-ollama-v1.1.0
feat-py-devtools-v1.3.0
feat-pytorch-v1.2.0
feat-rapids-v1.2.0
feat-transformers-v1.2.0
```

- [ ] **Step 6: Trigger a `build.yml` run after the features are published**

`workflow_dispatch` `build.yml` (or push any `images/**` no-op) so `build-bundles` assembles `base-ubuntu` / `base-cuda` against the freshly published feature `:latest` — closes the feature-publish lag window.

---

## Self-Review

**1. Spec coverage**

| Spec section | Task(s) |
|---|---|
| `features/homebrew` spec | Task 1 |
| `features/shell-kit` spec | Task 3 |
| `features/pixi` spec | Task 2 |
| Dependency edges summary | Tasks 3 (shell-kit→homebrew), 2 (pixi installsAfter), 8 (brew group), 9 (pixi group) |
| `base/Dockerfile` `full` removal + core brew removal | Task 4 |
| Bundle build-configs + `images/` metadata + `base-cuda-slim.json` | Task 5 |
| Image lineage table | Tasks 4 + 5 + 6 + 7 |
| `build.yml` two-phase + widened paths + feature-publish lag | Task 6, Task 13 Step 6 |
| `build-images.ps1` + `.tests.ps1` | Task 7 |
| Downstream features table | Tasks 8, 9 |
| Templates table + `image`-only change | Task 10 |
| Interactive-shell tradeoff note | Task 12 Step 2 |
| `dvt init` DEFAULT_IMAGE unchanged | No task needed — it is already `base-ubuntu`; Global Constraints + Task 12 note it stays. |
| `tests/test_static.py` changes | Tasks 4, 5, 6, 8, 9, 10 |
| `tests/features/` new files | Tasks 1, 2, 3 |
| `tests/integration/` | Task 11 |
| Versioning & docs | Tasks 8, 9, 12, 13 |
| Backward compatibility (bundle still published, same ref) | Tasks 5, 6 preserve refs; verified conceptually, no code |
| Rollout order | Task 13 |
| `dvt` dependency awareness | **Out of scope — Plan 2** (stated in header) |

`ramalama`: the spec flags it as "confirm during implementation". It has no `templates/` entry and was not in `tests/test_static.py`'s `FEATURES`. If `features/ramalama/devcontainer-feature.json` exists and runs a `pixi global`, add `dependsOn: pixi` + a minor bump as a follow-up step in Task 9; if it does not exist, nothing to do. Called out here rather than as a phantom task.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step has literal file content. Task 11 describes four named tests with explicit assertions and an explicit instruction to mirror the existing integration-test harness (which the executor must read first — that is a real step, not a placeholder). Task 7's "existing docker build path, unchanged" refers to code already in the file the executor is holding.

**3. Type/name consistency:**
- Feature refs: `ghcr.io/jesserobertson/devcontainers/{homebrew,shell-kit,pixi}` used identically in Tasks 2, 3, 5, 8, 9 and Global Constraints.
- `dependsOn` object form `{ "<ref>": {} }` consistent in Tasks 3, 8, 9 and the tests that assert it.
- `containerEnv` key sets match between each feature JSON (Tasks 1-3) and its test (same tasks).
- `IMAGES` list gains `base-cuda-slim` once (Task 5); `FEATURES` gains all three new names once (Task 1 Step 1); later tasks reference but never re-add them.
- `PIXI_DEPENDENT_FEATURES` (Task 9) and `BREW_FEATURES` (existing, used in Task 8) are disjoint and together with `agent`/`podman` cover every entry in `FEATURES` except the three new plumbing features.
- `build.yml` job names `build-slim` / `build-bundles` match between Task 6's YAML and Task 6 Step 3's test.
- `Invoke-DevcontainerBuild` parameter names (`ConfigDir`, `Tags`, `Push`) match between its definition and its call site (Task 7 Steps 5).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-shell-cli-features.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
