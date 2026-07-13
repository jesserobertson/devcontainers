# Claude Agent Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the shared devcontainers base image to a non-root `dev` user, then add a `claude-agent` feature that lets Claude Code run in opt-in unattended auto mode (`vibe` → `claude --dangerously-skip-permissions`) behind an egress-allowlist firewall, and wire it up for `kidinnu`.

**Architecture:** Phase 1 (Tasks 1-4) migrates `base/Dockerfile` and all 10 existing features from an all-root model to a single non-root `dev` user with zero default sudo. Phase 2 (Tasks 6-8) adds `features/claude-agent/` (native `claude` CLI install, an iptables/ipset egress-allowlist firewall adapted from `anthropics/claude-code`'s reference implementation, a `vibe` wrapper, and one narrowly-scoped sudoers exception) and wires it into `../kidinnu`.

**Tech Stack:** Docker, devcontainer features spec, bash, iptables/ipset, pytest (static + docker-backed tests), PowerShell (`build-images.ps1`, unrelated to this plan but present in the repo).

**Spec:** `docs/superpowers/specs/2026-07-13-claude-agent-containment-design.md`

## Global Constraints

- The non-root user is named `dev` everywhere (home `/home/dev`), replacing `linuxbrew`/`root`.
- `dev` gets **zero** passwordless sudo in the base image. The only exception anywhere is the one sudoers.d rule added by `features/claude-agent` itself, scoped to exactly `/usr/local/bin/init-firewall.sh`.
- Devcontainer features always execute `install.sh` as root regardless of `remoteUser` — this is a spec guarantee, not something any task should try to work around.
- Firewall allowlist is exactly: GitHub (via `api.github.com/meta` IP ranges), `api.anthropic.com`, `claude.ai`, `pypi.org`, `files.pythonhosted.org`, `conda.anaconda.org`, `repo.anaconda.com`. Default policy is DROP.
- `vibe` is the only opt-in entrypoint to `--dangerously-skip-permissions`. Plain `claude` always keeps normal approval prompts.
- kidinnu authenticates to GitHub via a fine-grained, repo-scoped PAT passed through `containerEnv`/`localEnv` — never the host's `gh`/ssh credentials.
- `ramalama/Dockerfile` (the LLM sidecar) intentionally stays root — it is explicitly out of scope for containment.

---

### Task 1: Migrate `base/Dockerfile` to a non-root `dev` user

**Files:**
- Modify: `base/Dockerfile`
- Test: `tests/test_static.py`

**Interfaces:**
- Produces: a `dev` user (home `/home/dev`, shell fish) with brew/pixi/dotfiles installed under it, zero passwordless sudo, image ends with `USER dev`. Every later task that touches this image assumes this user exists and is the default.

- [ ] **Step 1: Write failing static tests for the new Dockerfile shape**

Add to `tests/test_static.py` (near the top-level tests, after the existing per-feature parametrised block):

```python
def _dockerfile_text() -> str:
    return (REPO_ROOT / "base" / "Dockerfile").read_text()


def test_dockerfile_creates_dev_user():
    assert "useradd -m -s /bin/bash dev" in _dockerfile_text()


def test_dockerfile_no_passwordless_sudo():
    assert "NOPASSWD" not in _dockerfile_text()


def test_dockerfile_ends_as_dev_user():
    lines = [l for l in _dockerfile_text().splitlines() if l.strip()]
    assert lines[-1].strip() == "USER dev"


def test_dockerfile_pixi_envs_owned_by_dev():
    assert "chown dev:dev /opt/pixi-envs" in _dockerfile_text()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_static.py -k dockerfile -v`
Expected: 4 failures (Dockerfile still creates `linuxbrew`, still grants `NOPASSWD`, still ends `WORKDIR /workspace`, no `/opt/pixi-envs` chown).

- [ ] **Step 3: Rewrite `base/Dockerfile`**

Replace the full file with:

```dockerfile
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y \
    build-essential procps curl file git wget unzip sudo

# One dedicated non-root user for the whole image. Homebrew refuses to run
# as root anyway, and containment for features/claude-agent requires a
# non-root runtime user regardless. No passwordless sudo: the boundary
# must hold even if this user's session is compromised — see
# features/claude-agent for the one narrowly-scoped exception it adds for
# itself, nothing else.
RUN useradd -m -s /bin/bash dev
RUN su dev -c 'NONINTERACTIVE=1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'

ENV PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:$PATH"
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_NO_ANALYTICS=1

RUN su dev -c 'HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1 \
    /home/linuxbrew/.linuxbrew/bin/brew install \
    bat bat-extras eza fd fish fzf jq just neovim ripgrep starship zoxide'

RUN curl -fsSL https://pixi.sh/install.sh | su dev -s /bin/bash
RUN curl -fsLS get.chezmoi.io | sh -s -- -b /usr/local/bin
RUN su dev -c 'GIT_TERMINAL_PROMPT=0 chezmoi init --apply --no-tty --exclude=scripts \
    https://github.com/jesserobertson/dotfiles.git'

ENV PATH="/home/dev/.pixi/bin:/home/dev/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=300
ENV SHELL=/home/linuxbrew/.linuxbrew/bin/fish

# Shared pixi env store: features install as root (devcontainer-feature
# spec guarantee) but must leave envs usable by dev at runtime.
RUN mkdir -p /opt/pixi-envs && chown dev:dev /opt/pixi-envs

RUN chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev && \
    HOME=/home/dev git config --global --add safe.directory /workspace && \
    printf 'detached-environments = "/opt/pixi-envs"\n' > /home/dev/.pixi/config.toml && \
    mkdir -p /home/dev/.config/fish/conf.d && \
    printf 'if status is-interactive\n    eval (pixi shell-hook --manifest-path /workspace/pixi.toml --shell fish)\nend\n' \
    > /home/dev/.config/fish/conf.d/project-pixi.fish && \
    echo 'eval "$(pixi shell-hook --manifest-path /workspace/pixi.toml --shell bash)"' >> /home/dev/.bashrc && \
    chown -R dev:dev /home/dev

WORKDIR /workspace
USER dev
```

- [ ] **Step 4: Run the tests again to verify they pass**

Run: `pytest tests/test_static.py -k dockerfile -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add base/Dockerfile tests/test_static.py
git commit -m "feat: migrate base image to non-root dev user, drop passwordless sudo"
```

---

### Task 2: Wrap `pixi global install` calls in all features to run as `dev`

**Files:**
- Modify: `features/rapids/install.sh`, `features/jax/install.sh`, `features/pytorch/install.sh`, `features/mojo/install.sh`, `features/marimo/install.sh`, `features/fastapi/install.sh`, `features/cli/install.sh`, `features/py-devtools/install.sh`, `features/huggingface/install.sh`, `features/transformers/install.sh`, `features/ramalama/install.sh`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: the `dev` user from Task 1.
- Produces: every feature installs its packages into `dev`'s pixi environment instead of root's, so they're reachable at runtime by the `remoteUser: dev` session.

- [ ] **Step 1: Write a failing test covering all 11 features**

Add to `tests/test_static.py`:

```python
SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ramalama",
]


@pytest.mark.parametrize("feature", SU_DEV_FEATURES)
def test_pixi_calls_run_as_dev(feature):
    script = REPO_ROOT / "features" / feature / "install.sh"
    for line in script.read_text().splitlines():
        if "pixi global install" in line or ".pixi/envs/dev/bin/pip" in line:
            assert "su dev -c" in line, f"{feature}: not run via su dev -c: {line!r}"
```

Note: match on `.pixi/envs/dev/bin/pip` (no trailing `install`) — the original unwrapped line is
`"$HOME/.pixi/envs/dev/bin/pip" install ...`, where a `"` sits between `pip` and `install`, so a
substring check including `install` would fail to match the pre-fix line and the test would pass
vacuously instead of failing.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_static.py -k test_pixi_calls_run_as_dev -v`
Expected: 11 failures (none of the scripts wrap their pixi/pip calls yet).

- [ ] **Step 3: Edit each feature's install.sh**

`features/cli/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    typer rich pydantic pydantic-settings'
```

`features/fastapi/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    fastapi pydantic pydantic-settings uvicorn httpx'
```

`features/py-devtools/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    ruff mypy pytest pytest-cov \
    mkdocs mkdocs-material mkdocstrings mkdocstrings-python'
```

`features/huggingface/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    huggingface_hub tokenizers'
```

`features/transformers/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    transformers datasets accelerate tokenizers'
```

`features/marimo/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    marimo altair vega_datasets'
```

`features/mojo/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev \
    --channel "https://conda.modular.com/max-nightly/" \
    --channel conda-forge \
    modular'
```

`features/pytorch/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev \
    --channel pytorch \
    --channel nvidia \
    --channel conda-forge \
    pytorch torchvision "pytorch-cuda=12.4" marimo'
```

`features/jax/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev --channel conda-forge \
    marimo'

# jax CUDA 12 variant is only available via PyPI
su dev -c '/home/dev/.pixi/envs/dev/bin/pip install "jax[cuda12]>=0.4"'
```

`features/rapids/install.sh`:
```bash
#!/bin/bash
set -e

su dev -c 'pixi global install --environment dev \
    --channel conda-forge \
    --channel rapidsai \
    --channel nvidia \
    cudf'

# polars GPU extras are only on the NVIDIA PyPI index
su dev -c '/home/dev/.pixi/envs/dev/bin/pip install "polars[gpu]>=1.0" \
    --extra-index-url https://pypi.nvidia.com'
```

`features/ramalama/install.sh` (only the pixi line changes; the `/etc/profile.d` write stays root, unwrapped):
```bash
#!/bin/bash
set -e

HOST="${HOST:-host.docker.internal}"
PORT="${PORT:-8080}"
MODEL="${MODEL:-ollama://llama3.2}"
APIKEY="${APIKEY:-ramalama}"
CONTEXTSIZE="${CONTEXTSIZE:-4096}"

su dev -c 'pixi global install --environment dev --channel conda-forge openai'

cat > /etc/profile.d/ramalama.sh <<EOF
export RAMALAMA_HOST="${HOST}"
export RAMALAMA_PORT="${PORT}"
export RAMALAMA_MODEL="${MODEL}"
export OPENAI_BASE_URL="http://${HOST}:${PORT}/v1"
export OPENAI_API_KEY="${APIKEY}"
export RAMALAMA_CONTEXT_SIZE="${CONTEXTSIZE}"
EOF

chmod 644 /etc/profile.d/ramalama.sh
```

- [ ] **Step 4: Run the tests again to verify they pass**

Run: `pytest tests/test_static.py -k "test_pixi_calls_run_as_dev or test_install_sh_syntax" -v`
Expected: all passed (the existing `bash -n` syntax check must still pass too — double-check quoting didn't break any script).

- [ ] **Step 5: Commit**

```bash
git add features/*/install.sh tests/test_static.py
git commit -m "feat: run all feature pixi installs as the dev user"
```

---

### Task 3: Carve out `ramalama/Dockerfile` as explicitly root

**Files:**
- Modify: `ramalama/Dockerfile`
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: `base-cuda`, which after Task 1 defaults to `USER dev`.
- Produces: the ramalama sidecar image still runs as root (out of scope for containment — it's an inference server, not an agent-execution context).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_static.py`:

```python
def test_ramalama_dockerfile_explicit_root():
    content = (REPO_ROOT / "ramalama" / "Dockerfile").read_text()
    assert "USER root" in content
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_static.py -k test_ramalama_dockerfile_explicit_root -v`
Expected: FAIL (no `USER root` line yet).

- [ ] **Step 3: Edit `ramalama/Dockerfile`**

```dockerfile
FROM ghcr.io/jesserobertson/base-cuda:latest

# This sidecar is an inference server, not an agent-execution context, so
# it intentionally stays root rather than picking up base-cuda's non-root
# default user.
USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y python3-pip

# llama-cpp-python CUDA 12.4 wheels are compatible with CUDA 12.8
RUN pip install --break-system-packages ramalama \
    "llama-cpp-python[server]" \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

ENV RAMALAMA_MODEL=ollama://llama3.2
ENV RAMALAMA_PORT=8080

EXPOSE 8080

CMD ramalama serve --nocontainer ${RAMALAMA_MODEL} --port ${RAMALAMA_PORT}
```

- [ ] **Step 4: Run the test again to verify it passes**

Run: `pytest tests/test_static.py -k test_ramalama_dockerfile_explicit_root -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ramalama/Dockerfile tests/test_static.py
git commit -m "fix: keep ramalama sidecar explicitly root after base image migration"
```

---

### Task 4: Update the ramalama-sidecar example and README for `dev`

**Files:**
- Modify: `examples/ramalama-sidecar/.devcontainer/devcontainer.json`, `README.md`
- Test: `tests/test_static.py`

**Interfaces:**
- Produces: no remaining references to `remoteUser: root` or `/root/.cache/pixi` anywhere in the repo's examples/docs.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_static.py`:

```python
def _devcontainer_json(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text())


def test_ramalama_sidecar_example_remote_user_dev():
    data = _devcontainer_json("examples/ramalama-sidecar/.devcontainer/devcontainer.json")
    assert data["remoteUser"] == "dev"


def test_readme_no_root_remote_user():
    content = (REPO_ROOT / "README.md").read_text()
    assert '"remoteUser": "root"' not in content
    assert "/root/.cache/pixi" not in content
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_static.py -k "ramalama_sidecar_example_remote_user_dev or readme_no_root_remote_user" -v`
Expected: 2 failures.

- [ ] **Step 3: Edit `examples/ramalama-sidecar/.devcontainer/devcontainer.json`**

Change the `"remoteUser": "root"` line to `"remoteUser": "dev"`.

- [ ] **Step 4: Edit `README.md`**

In both the "GPU project" and "CPU project" example JSON blocks (the two under `## Using features in a project`), change:
- `"source=my-project-pixi-cache,target=/root/.cache/pixi,type=volume"` → `"source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"`
- `"remoteUser": "root"` → `"remoteUser": "dev"`

- [ ] **Step 5: Run the tests again to verify they pass**

Run: `pytest tests/test_static.py -k "ramalama_sidecar_example_remote_user_dev or readme_no_root_remote_user" -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add examples/ramalama-sidecar/.devcontainer/devcontainer.json README.md tests/test_static.py
git commit -m "docs: update examples and README for the dev user migration"
```

---

### Task 5: Manually verify the migrated base images build and behave correctly

**Files:** none (verification only)

**Interfaces:**
- Consumes: `base/Dockerfile` from Task 1, all `features/*/install.sh` from Task 2.
- Produces: confidence the migration actually works before building the agent feature on top of it. No automated test replaces this — it needs a real Docker build.

- [ ] **Step 1: Build base-ubuntu locally**

Run: `docker build -t base-ubuntu-test --build-arg BASE_IMAGE=ubuntu:24.04 base/`
Expected: build succeeds with no errors.

- [ ] **Step 2: Verify the runtime user and shell**

Run: `docker run --rm base-ubuntu-test whoami`
Expected output: `dev`

Run: `docker run --rm base-ubuntu-test bash -c 'echo $SHELL'`
Expected output: `/home/linuxbrew/.linuxbrew/bin/fish`

- [ ] **Step 3: Verify no passwordless sudo**

Run: `docker run --rm base-ubuntu-test sudo -n true`
Expected: non-zero exit, stderr mentions a password is required (confirms `NOPASSWD` really is gone).

- [ ] **Step 4: Verify pixi and brew work for `dev`**

Run: `docker run --rm base-ubuntu-test pixi --version`
Expected: a version string, no permission errors.

Run: `docker run --rm base-ubuntu-test brew --version`
Expected: a version string.

- [ ] **Step 5: Verify a representative feature installs and its binaries run as `dev`**

Build the `cli` feature on top (using the devcontainer CLI, if installed — `npm install -g @devcontainers/cli` if not):
```bash
devcontainer features package features/cli --output-folder .features-pkg
docker build -t base-ubuntu-cli-test --build-arg BASE_IMAGE=ubuntu:24.04 base/
docker run --rm base-ubuntu-cli-test bash -c \
  "su dev -c 'pixi global install --environment dev --channel conda-forge typer rich pydantic pydantic-settings' && su dev -c 'which typer 2>/dev/null; python3 -c \"import typer\"' "
```
Expected: no permission errors installing or importing `typer` as `dev`.

If any step fails, fix the root cause in `base/Dockerfile` or the relevant `install.sh` and re-run Tasks 1/2's automated tests plus this task's manual checks before continuing.

- [ ] **Step 6: No commit for this task** — it's verification only, not a code change. If fixes were needed, they were already committed as part of Task 1 or 2's amended commits.

---

### Task 6: Build the `claude-agent` feature

**Files:**
- Create: `features/claude-agent/devcontainer-feature.json`
- Create: `features/claude-agent/install.sh`
- Create: `features/claude-agent/init-firewall.sh`
- Create: `features/claude-agent/vibe`
- Modify: `tests/test_static.py`
- Create: `tests/features/test_claude_agent.py`

**Interfaces:**
- Consumes: the `dev` user (Task 1), `SU_DEV_FEATURES`-style conventions are not needed here since this feature doesn't use `pixi global install`.
- Produces: a composable feature other devcontainer.jsons (kidinnu, Task 8) reference as `ghcr.io/jesserobertson/devcontainers/claude-agent:latest`. Deploys `claude` and `vibe` to `dev`'s `$PATH`, `init-firewall.sh` to `/usr/local/bin`, and a scoped sudoers rule.

- [ ] **Step 1: Create `features/claude-agent/devcontainer-feature.json`**

```json
{
  "id": "claude-agent",
  "version": "1.0.0",
  "name": "Claude Agent (contained auto mode)",
  "description": "Installs Claude Code plus an egress-allowlist firewall and a `vibe` wrapper for opt-in unattended auto mode (--dangerously-skip-permissions). Consuming devcontainer.json must set runArgs: [\"--cap-add=NET_ADMIN\", \"--cap-add=NET_RAW\"] and postStartCommand: \"sudo /usr/local/bin/init-firewall.sh\".",
  "options": {}
}
```

- [ ] **Step 2: Create `features/claude-agent/init-firewall.sh`**

```bash
#!/bin/bash
set -euo pipefail
IFS=$'\n\t'

# 1. Extract Docker DNS info BEFORE any flushing
DOCKER_DNS_RULES=$(iptables-save -t nat | grep "127\.0\.0\.11" || true)

# Flush existing rules and delete existing ipsets
iptables -F
iptables -X
iptables -t nat -F
iptables -t nat -X
iptables -t mangle -F
iptables -t mangle -X
ipset destroy allowed-domains 2>/dev/null || true
rm -f /run/firewall-armed

# 2. Selectively restore ONLY internal Docker DNS resolution
if [ -n "$DOCKER_DNS_RULES" ]; then
    echo "Restoring Docker DNS rules..."
    iptables -t nat -N DOCKER_OUTPUT 2>/dev/null || true
    iptables -t nat -N DOCKER_POSTROUTING 2>/dev/null || true
    echo "$DOCKER_DNS_RULES" | xargs -L 1 iptables -t nat
else
    echo "No Docker DNS rules to restore"
fi

# Allow DNS and localhost before any restrictions
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A INPUT -p udp --sport 53 -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Create ipset with CIDR support
ipset create allowed-domains hash:net

# GitHub: fetch and aggregate their published IP ranges
echo "Fetching GitHub IP ranges..."
gh_ranges=$(curl -s https://api.github.com/meta)
if [ -z "$gh_ranges" ]; then
    echo "ERROR: Failed to fetch GitHub IP ranges"
    exit 1
fi
if ! echo "$gh_ranges" | jq -e '.web and .api and .git' >/dev/null; then
    echo "ERROR: GitHub API response missing required fields"
    exit 1
fi
echo "Processing GitHub IPs..."
while read -r cidr; do
    if [[ ! "$cidr" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}/[0-9]{1,2}$ ]]; then
        echo "ERROR: Invalid CIDR range from GitHub meta: $cidr"
        exit 1
    fi
    echo "Adding GitHub range $cidr"
    ipset add allowed-domains "$cidr"
done < <(echo "$gh_ranges" | jq -r '(.web + .api + .git)[]' | aggregate -q)

# Resolve and add the remaining allowed domains
for domain in \
    "api.anthropic.com" \
    "claude.ai" \
    "pypi.org" \
    "files.pythonhosted.org" \
    "conda.anaconda.org" \
    "repo.anaconda.com"; do
    echo "Resolving $domain..."
    ips=$(dig +noall +answer A "$domain" | awk '$4 == "A" {print $5}')
    if [ -z "$ips" ]; then
        echo "ERROR: Failed to resolve $domain"
        exit 1
    fi
    while read -r ip; do
        if [[ ! "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
            echo "ERROR: Invalid IP from DNS for $domain: $ip"
            exit 1
        fi
        echo "Adding $ip for $domain"
        ipset add allowed-domains "$ip"
    done < <(echo "$ips")
done

# Allow the Docker host network (so devcontainer tooling itself keeps working)
HOST_IP=$(ip route | grep default | cut -d" " -f3)
if [ -z "$HOST_IP" ]; then
    echo "ERROR: Failed to detect host IP"
    exit 1
fi
HOST_NETWORK=$(echo "$HOST_IP" | sed "s/\.[0-9]*$/.0\/24/")
echo "Host network detected as: $HOST_NETWORK"
iptables -A INPUT -s "$HOST_NETWORK" -j ACCEPT
iptables -A OUTPUT -d "$HOST_NETWORK" -j ACCEPT

# Default-DROP, then punch the specific holes we need
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited

echo "Firewall configuration complete"
echo "Verifying firewall rules..."
if curl --connect-timeout 5 https://example.com >/dev/null 2>&1; then
    echo "ERROR: Firewall verification failed - was able to reach https://example.com"
    exit 1
else
    echo "Firewall verification passed - unable to reach https://example.com as expected"
fi

if ! curl --connect-timeout 5 https://api.github.com/zen >/dev/null 2>&1; then
    echo "ERROR: Firewall verification failed - unable to reach https://api.github.com"
    exit 1
else
    echo "Firewall verification passed - able to reach https://api.github.com as expected"
fi

touch /run/firewall-armed
chmod 644 /run/firewall-armed
echo "Firewall armed."
```

- [ ] **Step 3: Create `features/claude-agent/vibe`**

```bash
#!/bin/bash
set -e
if [ ! -f /run/firewall-armed ]; then
    echo "error: egress firewall is not armed — refusing to run in auto mode" >&2
    exit 1
fi
exec claude --dangerously-skip-permissions "$@"
```

- [ ] **Step 4: Create `features/claude-agent/install.sh`**

```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# init-firewall.sh's runtime dependencies
apt-get update && apt-get install -y iptables ipset dnsutils jq aggregate

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
```

- [ ] **Step 5: Add `claude-agent` to the static parametrised feature tests**

In `tests/test_static.py`, change:

```python
FEATURES = ["huggingface", "transformers", "ramalama"]
```

to:

```python
FEATURES = ["huggingface", "transformers", "ramalama", "claude-agent"]
```

This gives `claude-agent` the existing `test_feature_json_has_required_fields`, `test_feature_json_id_matches_dir`, and `test_install_sh_syntax` checks for free. Add explicit syntax checks for the other two scripts (not named `install.sh` so not covered by the parametrised one):

```python
@pytest.mark.parametrize("script", ["init-firewall.sh", "vibe"])
def test_claude_agent_script_syntax(script):
    path = REPO_ROOT / "features" / "claude-agent" / script
    result = subprocess.run(
        ["bash", "-n", "-"],
        input=path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_claude_agent_no_options():
    assert _feature_json("claude-agent").get("options", {}) == {}
```

Run: `pytest tests/test_static.py -k claude_agent -v`
Expected: PASS — Steps 1-4 already created `devcontainer-feature.json`, `init-firewall.sh`, and
`vibe`, so this step is confirming their structure/syntax, not driving their creation. If it
fails, fix the file from the relevant earlier step before moving on.

- [ ] **Step 6: Create `tests/features/test_claude_agent.py`**

```python
"""Docker tests for the claude-agent feature: install.sh side effects and a live firewall exercise."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "claude-agent"

CURL_STUB = """#!/bin/bash
if [[ "$*" == *claude.ai/install.sh* ]]; then
    echo 'mkdir -p ~/.local/bin && touch ~/.local/bin/claude && chmod +x ~/.local/bin/claude'
fi
"""


# ---------------------------------------------------------------------------
# install.sh side effects (stubbed curl, no real network)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def installed_container(tmp_path_factory):
    cid = subprocess.check_output(
        ["docker", "run", "-d", "ubuntu:24.04", "sleep", "infinity"], text=True
    ).strip()
    try:
        _prepare(cid, tmp_path_factory.mktemp("claude-agent-stub"))
        subprocess.run(
            ["docker", "exec", cid, "bash", "/tmp/claude-agent/install.sh"],
            check=True,
        )
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_init_firewall_installed_root_only(installed_container):
    assert _stat(installed_container, "/usr/local/bin/init-firewall.sh") == "700 root root"


def test_vibe_installed_executable_by_dev(installed_container):
    assert _stat(installed_container, "/home/dev/.local/bin/vibe") == "755 dev dev"


def test_vibe_execs_claude_with_skip_permissions(installed_container):
    content = _exec(installed_container, "cat /home/dev/.local/bin/vibe")
    assert "--dangerously-skip-permissions" in content


def test_sudoers_rule_scoped_to_firewall_script(installed_container):
    content = _exec(installed_container, "cat /etc/sudoers.d/claude-agent")
    assert content.strip() == "dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh"


def test_sudoers_file_permissions(installed_container):
    assert _stat(installed_container, "/etc/sudoers.d/claude-agent") == "440 root root"


def test_claude_installed_for_dev_user(installed_container):
    result = subprocess.run(
        ["docker", "exec", installed_container, "test", "-f", "/home/dev/.local/bin/claude"],
        capture_output=True,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Live firewall exercise: does it actually block/allow the right hosts?
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def firewalled_container():
    cid = subprocess.check_output(
        ["docker", "run", "-d", "--cap-add=NET_ADMIN", "--cap-add=NET_RAW",
         "ubuntu:24.04", "sleep", "infinity"],
        text=True,
    ).strip()
    try:
        subprocess.run(
            ["docker", "exec", cid, "bash", "-c",
             "apt-get update && apt-get install -y iptables ipset dnsutils jq aggregate curl"],
            check=True,
        )
        subprocess.run(
            ["docker", "cp", str(FEATURE_DIR / "init-firewall.sh"),
             f"{cid}:/usr/local/bin/init-firewall.sh"],
            check=True,
        )
        subprocess.run(["docker", "exec", cid, "chmod", "+x", "/usr/local/bin/init-firewall.sh"], check=True)
        subprocess.run(["docker", "exec", cid, "/usr/local/bin/init-firewall.sh"], check=True)
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_firewall_blocks_non_allowlisted_host(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://example.com"],
        capture_output=True,
    )
    assert result.returncode != 0


def test_firewall_allows_github(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://api.github.com/zen"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_firewall_armed_marker_written(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "test", "-f", "/run/firewall-armed"],
        capture_output=True,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepare(cid: str, tmp_path: Path) -> None:
    """Create the dev user, stub curl for the claude.ai installer, copy the feature in."""
    subprocess.run(["docker", "exec", cid, "useradd", "-m", "-s", "/bin/bash", "dev"], check=True)

    stub = tmp_path / "curl"
    stub.write_text(CURL_STUB)
    stub.chmod(0o755)
    subprocess.run(["docker", "cp", str(stub), f"{cid}:/usr/local/bin/curl"], check=True)

    subprocess.run(["docker", "exec", cid, "mkdir", "-p", "/tmp/claude-agent"], check=True)
    for name in ("install.sh", "init-firewall.sh", "vibe", "devcontainer-feature.json"):
        subprocess.run(
            ["docker", "cp", str(FEATURE_DIR / name), f"{cid}:/tmp/claude-agent/{name}"],
            check=True,
        )
    subprocess.run(["docker", "exec", cid, "chmod", "+x", "/tmp/claude-agent/install.sh"], check=True)


def _exec(cid: str, cmd: str) -> str:
    return subprocess.check_output(["docker", "exec", cid, "bash", "-c", cmd], text=True).strip()


def _stat(cid: str, path: str) -> str:
    return _exec(cid, f"stat -c '%a %U %G' {path}")
```

- [ ] **Step 7: Run all claude-agent tests to verify they pass**

Run: `pytest tests/test_static.py tests/features/test_claude_agent.py -k claude_agent -v`
Expected: all passed. The `firewalled_container` tests need real internet access (they resolve and hit `api.github.com`) and `--cap-add=NET_ADMIN` support from the Docker daemon — both should be available under Docker Desktop.

- [ ] **Step 8: Commit**

```bash
git add features/claude-agent tests/test_static.py tests/features/test_claude_agent.py
git commit -m "feat: add claude-agent feature (contained auto mode via vibe)"
```

---

### Task 7: Document `claude-agent` in the README

**Files:**
- Modify: `README.md`
- Test: `tests/test_static.py`

**Interfaces:**
- Produces: the features table, repo structure tree, and a new section explaining `vibe`/firewall/sudo scope, so someone reading the README understands the containment story without reading the spec.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_static.py`:

```python
def test_readme_documents_claude_agent():
    assert "claude-agent" in (REPO_ROOT / "README.md").read_text()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_static.py -k test_readme_documents_claude_agent -v`
Expected: FAIL.

- [ ] **Step 3: Add a row to the Features table**

In the `## Features` table in `README.md`, add:

```markdown
| `…/claude-agent:latest` | Agent | base-ubuntu / base-cuda | Contained Claude Code — native `claude` CLI, egress-allowlist firewall, `vibe` for opt-in unattended auto mode |
```

- [ ] **Step 4: Add `claude-agent/` to the repo structure tree**

In the `## Repo structure` code block, add a line under the `features/` list:

```
  claude-agent/              ← Agent: contained Claude Code (firewall + vibe auto-mode wrapper)
```

- [ ] **Step 5: Add a new section explaining contained auto mode**

Add a new `## Contained auto mode (claude-agent)` section, placed after `## Local LLM (ramalama)` and before `## Adding a new feature`:

```markdown
## Contained auto mode (claude-agent)

The `claude-agent` feature installs Claude Code plus an egress-allowlist firewall, so
unattended sessions (`--dangerously-skip-permissions`) have the container itself — not
model judgment — as the safety boundary. See
[Anthropic's containment writeup](https://www.anthropic.com/engineering/how-we-contain-claude)
for the reasoning.

```json
{
  "features": {
    "ghcr.io/jesserobertson/devcontainers/claude-agent:latest": {}
  },
  "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
  "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
  "waitFor": "postStartCommand",
  "remoteUser": "dev"
}
```

- `claude` — normal Claude Code, with approval prompts, same as anywhere else.
- `vibe` — the opt-in unattended entrypoint (`claude --dangerously-skip-permissions`).
  Refuses to start unless the egress firewall actually armed on container start.
- Network egress is default-DROP, allowlisting only GitHub, `api.anthropic.com`,
  `claude.ai`, and the PyPI/conda-forge package hosts.
- `dev` has no sudo access anywhere in this image except one scoped rule this feature
  adds for itself: `/usr/local/bin/init-firewall.sh`, nothing else.
```

- [ ] **Step 6: Run the test again to verify it passes**

Run: `pytest tests/test_static.py -k test_readme_documents_claude_agent -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md tests/test_static.py
git commit -m "docs: document the claude-agent feature and contained auto mode"
```

---

### Task 8: Wire up `kidinnu/.devcontainer`

**Files:**
- Create: `../kidinnu/.devcontainer/devcontainer.json`
- Create: `../kidinnu/.devcontainer/README.md`

**Interfaces:**
- Consumes: `ghcr.io/jesserobertson/devcontainers/claude-agent:latest` and `.../py-devtools:latest` (published from Tasks 1-7 once merged to `main` — see note in Step 3).

- [ ] **Step 1: Create `../kidinnu/.devcontainer/devcontainer.json`**

```json
{
  "name": "kidinnu",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {},
    "ghcr.io/jesserobertson/devcontainers/claude-agent:latest": {}
  },
  "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
  "mounts": [
    "source=kidinnu-pixi-cache,target=/home/dev/.cache/pixi,type=volume",
    "source=kidinnu-claude-config,target=/home/dev/.claude,type=volume",
    "source=kidinnu-claude-bashhistory,target=/commandhistory,type=volume"
  ],
  "containerEnv": {
    "ANTHROPIC_API_KEY": "${localEnv:ANTHROPIC_API_KEY}",
    "GH_TOKEN": "${localEnv:KIDINNU_GH_TOKEN}"
  },
  "postCreateCommand": "pixi install",
  "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
  "waitFor": "postStartCommand",
  "remoteUser": "dev"
}
```

- [ ] **Step 2: Create `../kidinnu/.devcontainer/README.md`**

```markdown
# kidinnu devcontainer

Uses the `claude-agent` feature from
[jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers) for
contained, opt-in unattended Claude Code sessions.

## Before first use

Set these in your **host** shell profile (never commit them):

- `ANTHROPIC_API_KEY` — your Claude API key.
- `KIDINNU_GH_TOKEN` — a fine-grained GitHub PAT scoped to `jesserobertson/kidinnu` only
  (Contents: read/write, Pull requests: read/write; no other repos, no admin scopes).
  Create one at https://github.com/settings/personal-access-tokens/new.

## Usage

- `claude` — normal Claude Code session with approval prompts.
- `vibe` — unattended auto mode (`claude --dangerously-skip-permissions`). Refuses to
  start if the egress firewall didn't arm when the container started — check the
  `postStartCommand` output (`docker logs`, or your devcontainer tool's equivalent) if
  it does.
```

- [ ] **Step 3: Validate JSON syntax**

Run: `python -m json.tool ../kidinnu/.devcontainer/devcontainer.json`
Expected: pretty-printed JSON, no parse error.

Note: this devcontainer.json references `ghcr.io/jesserobertson/devcontainers/claude-agent:latest`, `.../py-devtools:latest`, and `ghcr.io/jesserobertson/base-ubuntu:latest`. Those image/feature tags are only actually resolvable once Tasks 1-7 are merged to `main` in the devcontainers repo and the publish workflows (`.github/workflows/build.yml`, `publish-features.yml`) have run. Task 9's build will fail with an image/feature-not-found error until that's happened — that's expected, not a bug in this task.

- [ ] **Step 4: Commit (in the kidinnu repo)**

```bash
cd ../kidinnu
git add .devcontainer/devcontainer.json .devcontainer/README.md
git commit -m "feat: add devcontainer using the claude-agent containment feature"
```

---

### Task 9: End-to-end verification in kidinnu

**Files:** none (verification only)

**Interfaces:**
- Consumes: the published `claude-agent` and `py-devtools` features/images from Tasks 1-8, once merged and built.

- [ ] **Step 1: Bring the container up**

Prerequisite: `npm install -g @devcontainers/cli` if not already installed.

Run (from `../kidinnu`): `devcontainer up --workspace-folder .`
Expected: succeeds; `postStartCommand` output shows `"Firewall configuration complete"` and `"Firewall armed."`.

- [ ] **Step 2: Verify the runtime user has no broad sudo**

Run: `devcontainer exec --workspace-folder . whoami`
Expected: `dev`

Run: `devcontainer exec --workspace-folder . sudo -l`
Expected: lists exactly one entry, `/usr/local/bin/init-firewall.sh` — nothing else.

- [ ] **Step 3: Verify the firewall marker and behavior**

Run: `devcontainer exec --workspace-folder . test -f /run/firewall-armed && echo armed`
Expected output: `armed`

Run: `devcontainer exec --workspace-folder . curl --connect-timeout 5 -o /dev/null -s -w '%{http_code}\n' https://example.com`
Expected: command fails / times out (connection refused by the firewall), not a `200`.

Run: `devcontainer exec --workspace-folder . curl --connect-timeout 5 -o /dev/null -s -w '%{http_code}\n' https://api.github.com`
Expected output: `200`

- [ ] **Step 4: Verify both Claude entrypoints**

Run: `devcontainer exec --workspace-folder . claude --version`
Expected: a version string (plain `claude` still works normally, with approval prompts for any actual session).

Run: `devcontainer exec --workspace-folder . vibe --version`
Expected: same version string (confirms `vibe` execs through to `claude` when the firewall is armed).

- [ ] **Step 5: Verify credentials are scoped, not leaked from the host**

Run: `devcontainer exec --workspace-folder . bash -c 'echo ${GH_TOKEN:0:4}'`
Expected: a short non-empty prefix (confirms `KIDINNU_GH_TOKEN` made it through as `GH_TOKEN`) — do not print the full token.

Run: `devcontainer exec --workspace-folder . bash -c 'ls ~/.ssh 2>&1; ls ~/.aws 2>&1'`
Expected: both report "No such file or directory" — confirms no host credential directories leaked in via unintended mounts.

If any check fails, treat it as a real bug in Tasks 1-8 (not this task) — go back, fix the root cause, and re-run this task's checks from Step 1.

- [ ] **Step 6: No commit for this task** — verification only.
