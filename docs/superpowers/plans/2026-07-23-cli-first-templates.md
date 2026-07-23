# CLI-First Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every feature in this repo a complete, standalone `devcontainer.json` under `templates/`, so a terminal-first workflow (`devpod up`/`devpod ssh`, or `npx @devcontainers/cli`) works without hand-copying JSON out of the README — and remove the dead `pgrep sshd` wait-loop confirmed in the design spec to never have matched anything.

**Architecture:** Twelve new static JSON files (one per `features/*` directory), each a fully runnable devcontainer config (correct base image, that one feature, `pixi install`, `remoteUser: dev`). No new code, no runtime component — this is content plus a README rewrite plus static test coverage, matching the repo's existing `tests/test_static.py` convention (JSON/YAML/bash validation, no Docker required).

**Tech Stack:** JSON (devcontainer configs), Markdown (README), Python/pytest (`tests/test_static.py`) for validation — the same stack `tests/test_static.py` already uses for `features/*/devcontainer-feature.json` and `examples/**/devcontainer.json`.

## Global Constraints

- Every template: `remoteUser: "dev"`, `postCreateCommand: "pixi install"`, a `<name>-pixi-cache` volume mount at `/home/dev/.cache/pixi`, and feature reference `ghcr.io/jesserobertson/devcontainers/<name>:latest` — matching the existing README convention (`docs/superpowers/specs/2026-07-23-cli-first-templates-design.md`).
- GPU features (`rapids`, `mojo`, `jax`, `pytorch`, `transformers`) use `ghcr.io/jesserobertson/base-cuda:latest` and `"runArgs": ["--gpus", "all"]`. Everything else uses `ghcr.io/jesserobertson/base-ubuntu:latest`.
- `agent` additionally needs `"runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]`, `"postStartCommand": "sudo /usr/local/bin/init-firewall.sh"`, `"waitFor": "postStartCommand"` — per `features/agent/devcontainer-feature.json`'s documented requirement.
- No template, and no file in this repo, may contain the string `pgrep sshd` (confirmed dead code — DevPod's in-container SSH server is an embedded Go binary, `cmd/agent/container/ssh_server.go`, never a process named `sshd`).
- Tests go in `tests/test_static.py` (pytest), following that file's existing helper pattern (`_feature_json`, `_devcontainer_json`, `_yaml` at the bottom) — not a new Pester file. `tests/test_static.py` is the file that already validates `examples/**/devcontainer.json` and feature JSON; templates are the same kind of static artifact.
- Run tests with `pixi run pytest tests/test_static.py -v` (confirmed working: `pixi run pytest --version` → `pytest 9.1.1`).

---

### Task 1: GPU templates (rapids, mojo, jax, pytorch, transformers)

**Files:**
- Create: `templates/rapids/devcontainer.json`
- Create: `templates/mojo/devcontainer.json`
- Create: `templates/jax/devcontainer.json`
- Create: `templates/pytorch/devcontainer.json`
- Create: `templates/transformers/devcontainer.json`
- Modify: `tests/test_static.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `templates/<feature>/devcontainer.json` on disk for the 5 GPU features; a `GPU_TEMPLATE_FEATURES` list and a `_template_json(feature)` helper in `tests/test_static.py` that Tasks 2–5 reuse (`_template_json` returns `json.loads((REPO_ROOT / "templates" / feature / "devcontainer.json").read_text())`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_static.py`, right after the existing `SU_DEV_FEATURES` constant near the top:

```python
GPU_TEMPLATE_FEATURES = ["rapids", "mojo", "jax", "pytorch", "transformers"]
```

Add near the bottom, next to the other helpers (`_feature_json`, `_devcontainer_json`, `_yaml`):

```python
def _template_json(feature: str) -> dict:
    path = REPO_ROOT / "templates" / feature / "devcontainer.json"
    return json.loads(path.read_text())
```

Add a new section (after the `# --- example devcontainer configs ---` section, before `# --- compose YAML ---`):

```python
# --- templates/ (standalone per-feature devcontainer.json) ---

@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_uses_base_cuda(feature):
    assert _template_json(feature)["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_requests_gpus(feature):
    assert _template_json(feature)["runArgs"] == ["--gpus", "all"]


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_static.py -v -k gpu_template`
Expected: FAIL — `FileNotFoundError` (no `templates/` directory exists yet).

- [ ] **Step 3: Create the 5 GPU template files**

`templates/rapids/devcontainer.json`:

```json
{
  "name": "rapids",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/rapids:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=rapids-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/mojo/devcontainer.json`:

```json
{
  "name": "mojo",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/mojo:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=mojo-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/jax/devcontainer.json`:

```json
{
  "name": "jax",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/jax:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=jax-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/pytorch/devcontainer.json`:

```json
{
  "name": "pytorch",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/pytorch:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=pytorch-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/transformers/devcontainer.json`:

```json
{
  "name": "transformers",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=transformers-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_static.py -v -k gpu_template`
Expected: PASS (25 tests: 5 features × 5 test functions)

- [ ] **Step 5: Commit**

```bash
git add templates/rapids templates/mojo templates/jax templates/pytorch templates/transformers tests/test_static.py
git commit -m "feat: add dev CLI templates for GPU features"
```

---

### Task 2: CPU templates (marimo, fastapi, cli, py-devtools, huggingface, ollama)

**Files:**
- Create: `templates/marimo/devcontainer.json`
- Create: `templates/fastapi/devcontainer.json`
- Create: `templates/cli/devcontainer.json`
- Create: `templates/py-devtools/devcontainer.json`
- Create: `templates/huggingface/devcontainer.json`
- Create: `templates/ollama/devcontainer.json`
- Modify: `tests/test_static.py`

**Interfaces:**
- Consumes: `_template_json(feature)` helper from Task 1 (`tests/test_static.py`).
- Produces: `templates/<feature>/devcontainer.json` on disk for the 6 CPU features; a `CPU_TEMPLATE_FEATURES` list Task 4/5 don't need but keeps this task's tests scoped correctly.

- [ ] **Step 1: Write the failing tests**

Add next to `GPU_TEMPLATE_FEATURES` in `tests/test_static.py`:

```python
CPU_TEMPLATE_FEATURES = ["marimo", "fastapi", "cli", "py-devtools", "huggingface", "ollama"]
```

Add to the `# --- templates/ ---` section, after the GPU template tests:

```python
@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_uses_base_ubuntu(feature):
    assert _template_json(feature)["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_static.py -v -k cpu_template`
Expected: FAIL — `FileNotFoundError` (none of the 6 files exist yet).

- [ ] **Step 3: Create the 6 CPU template files**

`templates/marimo/devcontainer.json`:

```json
{
  "name": "marimo",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/marimo:latest": {}
  },
  "mounts": [
    "source=marimo-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/fastapi/devcontainer.json`:

```json
{
  "name": "fastapi",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}
  },
  "mounts": [
    "source=fastapi-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/cli/devcontainer.json`:

```json
{
  "name": "cli",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/cli:latest": {}
  },
  "mounts": [
    "source=cli-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/py-devtools/devcontainer.json`:

```json
{
  "name": "py-devtools",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "mounts": [
    "source=py-devtools-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/huggingface/devcontainer.json`:

```json
{
  "name": "huggingface",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {}
  },
  "mounts": [
    "source=huggingface-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

`templates/ollama/devcontainer.json`:

```json
{
  "name": "ollama",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/ollama:latest": {}
  },
  "mounts": [
    "source=ollama-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_static.py -v -k cpu_template`
Expected: PASS (24 tests: 6 features × 4 test functions)

- [ ] **Step 5: Commit**

```bash
git add templates/marimo templates/fastapi templates/cli templates/py-devtools templates/huggingface templates/ollama tests/test_static.py
git commit -m "feat: add dev CLI templates for CPU features"
```

---

### Task 3: agent template

**Files:**
- Create: `templates/agent/devcontainer.json`
- Modify: `tests/test_static.py`

**Interfaces:**
- Consumes: `_template_json(feature)` helper from Task 1.
- Produces: `templates/agent/devcontainer.json` — the last template file, completing the set all 12 `FEATURES` (from the existing `FEATURES` constant at the top of `tests/test_static.py`) now have a `templates/<feature>/` entry.

- [ ] **Step 1: Write the failing test**

Add to the `# --- templates/ ---` section in `tests/test_static.py`, after the CPU template tests:

```python
def test_agent_template_uses_base_ubuntu():
    assert _template_json("agent")["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_agent_template_references_own_feature():
    data = _template_json("agent")
    assert "ghcr.io/jesserobertson/devcontainers/agent:latest" in data["features"]


def test_agent_template_remote_user_dev():
    assert _template_json("agent")["remoteUser"] == "dev"


def test_agent_template_declares_firewall_caps():
    run_args = _template_json("agent")["runArgs"]
    assert "--cap-add=NET_ADMIN" in run_args
    assert "--cap-add=NET_RAW" in run_args


def test_agent_template_arms_firewall_on_start():
    data = _template_json("agent")
    assert data["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert data["waitFor"] == "postStartCommand"


def test_agent_template_no_sshd_waitloop():
    assert "pgrep sshd" not in json.dumps(_template_json("agent"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_static.py -v -k agent_template`
Expected: FAIL — `FileNotFoundError` (`templates/agent/devcontainer.json` doesn't exist yet).

- [ ] **Step 3: Create the agent template file**

`templates/agent/devcontainer.json`:

```json
{
  "name": "agent",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
  },
  "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
  "mounts": [
    "source=agent-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
  "waitFor": "postStartCommand",
  "remoteUser": "dev"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_static.py -v -k agent_template`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full template test set to confirm all 12 are covered**

Run: `pixi run pytest tests/test_static.py -v -k template`
Expected: PASS (55 tests total: 25 GPU + 24 CPU + 6 agent)

- [ ] **Step 6: Commit**

```bash
git add templates/agent tests/test_static.py
git commit -m "feat: add dev CLI template for agent feature"
```

---

### Task 4: README restructure — collapse examples, add CLI usage section

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: `templates/` directory from Tasks 1–3 (referenced by path in prose, not executed).
- Produces: none consumed by later tasks — this is a leaf change. `tests/test_static.py::test_readme_no_root_remote_user` and `test_readme_documents_agent` (already existing, unmodified) must still pass after this edit.

- [ ] **Step 1: Replace the GPU/CPU example blocks with one example + template pointers + CLI section**

In `README.md`, find this exact block (the two full JSON examples between `## Using features in a project` and `### How it works`):

```
**GPU project (e.g. rapids):**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/rapids:latest": {},
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "postStartCommand": "until pgrep sshd > /dev/null 2>&1; do sleep 1; done",
  "remoteUser": "dev"
}
```

**CPU project (e.g. FastAPI service):**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "mounts": [
    "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "postStartCommand": "until pgrep sshd > /dev/null 2>&1; do sleep 1; done",
  "remoteUser": "dev"
}
```
```

Replace it with:

```
**Example (FastAPI service):**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "workspaceMount": "source=${localWorkspaceFolder},target=/workspace,type=bind,consistency=cached",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "mounts": [
    "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

Every other feature (GPU: `rapids`, `mojo`, `jax`, `pytorch`, `transformers` · CPU: `marimo`,
`cli`, `py-devtools`, `huggingface`, `ollama` · `agent` for contained auto mode) has a
complete, ready-to-copy `devcontainer.json` under [`templates/`](templates/) instead of a
repeated block here. `marimo`'s template uses `base-ubuntu`; swap in `base-cuda` (and add
`"runArgs": ["--gpus", "all"]`) if you want GPU-accelerated plotting backends.

### Using with a CLI

No VS Code required. Copy a template into your project and drive it with
[DevPod](https://devpod.sh) or the official
[`@devcontainers/cli`](https://github.com/devcontainers/cli):

```bash
mkdir -p my-project/.devcontainer
cp templates/fastapi/devcontainer.json my-project/.devcontainer/devcontainer.json

devpod up my-project        # or: npx @devcontainers/cli up --workspace-folder my-project
devpod ssh my-project        # or: npx @devcontainers/cli exec --workspace-folder my-project -- bash
```
```

Use the Edit tool with the "find" text above as `old_string` and the "replace" text as `new_string` — it's a single contiguous block in the file so no line-number ambiguity.

- [ ] **Step 2: Confirm no sshd string remains in README**

Run: `grep -c "pgrep sshd" README.md`
Expected: command exits with status 1 (no matches) — `grep -c` prints `0` and returns non-zero when nothing matches.

- [ ] **Step 3: Run the existing README-related static tests**

Run: `pixi run pytest tests/test_static.py -v -k readme`
Expected: PASS (`test_readme_no_root_remote_user`, `test_readme_documents_agent` both still pass — this edit didn't touch `remoteUser` or the word "agent")

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: replace per-combo JSON blocks with templates/ pointers and a CLI usage section"
```

---

### Task 5: Remove sshd line from examples/, add repo-wide regression test

**Files:**
- Modify: `examples/ollama-sidecar/.devcontainer/devcontainer.json`
- Modify: `tests/test_static.py`

**Interfaces:**
- Consumes: nothing new — this is the final sweep, run after README (Task 4) and all templates (Tasks 1–3) are already clean.
- Produces: nothing consumed further — this is the last task in the plan.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_static.py`, in the `# --- example devcontainer configs ---` section (next to `test_ollama_sidecar_example_remote_user_dev`):

```python
def test_no_pgrep_sshd_anywhere():
    # Confirmed dead code: DevPod's in-container SSH server is an embedded Go
    # binary (cmd/agent/container/ssh_server.go), never a process named sshd.
    # See docs/superpowers/specs/2026-07-23-cli-first-templates-design.md.
    offenders = []
    if "pgrep sshd" in (REPO_ROOT / "README.md").read_text():
        offenders.append("README.md")
    for path in sorted(REPO_ROOT.glob("examples/**/devcontainer.json")):
        if "pgrep sshd" in path.read_text():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    for path in sorted(REPO_ROOT.glob("templates/**/devcontainer.json")):
        if "pgrep sshd" in path.read_text():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"pgrep sshd wait-loop found in: {offenders}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_static.py -v -k test_no_pgrep_sshd_anywhere`
Expected: FAIL — `examples/ollama-sidecar/.devcontainer/devcontainer.json` still has the line (README and `templates/` are already clean from Tasks 1–4, so it's the only offender listed).

- [ ] **Step 3: Remove the line from the ollama-sidecar example**

In `examples/ollama-sidecar/.devcontainer/devcontainer.json`, remove this line entirely:

```json
  "postStartCommand": "until pgrep sshd > /dev/null 2>&1; do sleep 1; done",
```

The file goes from:

```json
{
  "name": "my-project",
  "dockerComposeFile": "docker-compose.yml",
  "service": "app",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {},
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {},
    "ghcr.io/jesserobertson/devcontainers/ollama:latest": {
      "host": "ollama",
      "port": "11434",
      "model": "llama3.2"
    },
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "postCreateCommand": "pixi install",
  "postStartCommand": "until pgrep sshd > /dev/null 2>&1; do sleep 1; done",
  "remoteUser": "dev"
}
```

to:

```json
{
  "name": "my-project",
  "dockerComposeFile": "docker-compose.yml",
  "service": "app",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {},
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {},
    "ghcr.io/jesserobertson/devcontainers/ollama:latest": {
      "host": "ollama",
      "port": "11434",
      "model": "llama3.2"
    },
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "postCreateCommand": "pixi install",
  "remoteUser": "dev"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run pytest tests/test_static.py -v -k test_no_pgrep_sshd_anywhere`
Expected: PASS

- [ ] **Step 5: Run the full static test suite**

Run: `pixi run pytest tests/test_static.py -v`
Expected: PASS — all tests green, including the 55 template tests from Tasks 1–3, the README tests from Task 4, and every pre-existing test in the file (feature JSON checks, install.sh syntax, compose YAML, Dockerfile checks). No pre-existing test should have changed behavior — this plan only adds files and removes one dead line.

- [ ] **Step 6: Commit**

```bash
git add examples/ollama-sidecar/.devcontainer/devcontainer.json tests/test_static.py
git commit -m "fix: remove dead sshd wait-loop from ollama-sidecar example, add regression test"
```

---

## Self-Review Notes

- **Spec coverage:** `templates/` directory (Tasks 1–3) ✓, README changes — sshd removal + collapsed examples + CLI section (Task 4) ✓, `examples/ollama-sidecar` sshd removal (Task 5) ✓, tests validating JSON validity/feature references/no-sshd-regression (Tasks 1, 2, 3, 5) ✓. The spec's suggestion of a new `templates.tests.ps1` Pester file was deliberately not followed — `tests/test_static.py` (pytest) is the actual existing convention for validating JSON/example configs in this repo (see `test_ollama_sidecar_example_remote_user_dev` etc. already there), so extending it keeps one validation story instead of two.
- **Placeholder scan:** no TBD/TODO; every step shows complete file content or exact test code.
- **Type consistency:** `_template_json(feature)` defined once in Task 1, reused verbatim (same name, same signature) in Tasks 2, 3, 5. `FEATURES`, `REPO_ROOT`, `_devcontainer_json` reused from the file's existing top-of-file definitions, unchanged.
