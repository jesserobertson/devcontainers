# devcontainers

Base images and composable devcontainer features for Python development, published to `ghcr.io/jesserobertson`. All images include fish shell, starship, neovim, and pixi — with the full dotfiles setup from [jesserobertson/dotfiles](https://github.com/jesserobertson/dotfiles) baked in.

## Base images

| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |

## Features

Composable features that install on top of a base image at container creation time. Combine freely.

| Feature | Use | Stack | Description |
|---------|-----|-------|-------------|
| `…/rapids:latest` | ML | base-cuda | GPU DataFrames and array computing — cuDF, Polars GPU |
| `…/jax:latest` | ML | base-cuda | Accelerated numerical computing — JAX (CUDA 12), Marimo |
| `…/pytorch:latest` | ML | base-cuda | Deep learning — PyTorch (CUDA 12.4), Torchvision, Marimo |
| `…/mojo:latest` | ML | base-cuda | Systems AI programming — Modular MAX / Mojo (nightly) |
| `…/marimo:latest` | Data | base-ubuntu / base-cuda | Reactive notebooks and visualisation — Marimo, Altair, vega_datasets |
| `…/fastapi:latest` | Web | base-ubuntu / base-cuda | REST APIs — FastAPI, Pydantic, Uvicorn, httpx |
| `…/cli:latest` | CLI | base-ubuntu / base-cuda | Command-line tools — Typer, Rich, Pydantic, pydantic-settings |
| `…/py-devtools:latest` | Dev | base-ubuntu / base-cuda | Python dev tooling — ruff, mypy, pytest, pytest-cov, mkdocs, mkdocs-material, mkdocstrings |
| `…/huggingface:latest` | ML | base-ubuntu / base-cuda | HuggingFace tooling — huggingface_hub, tokenizers; sets HF_HOME |
| `…/transformers:latest` | ML | base-cuda | HuggingFace inference — transformers, datasets, accelerate |
| `…/ollama:latest` | ML | base-ubuntu / base-cuda | Local LLM client — OpenAI-compatible client for an Ollama service |
| `…/agent:latest` | Agent | base-ubuntu / base-cuda | Contained agents — `claude`/`pi`/`omp` CLIs, egress-allowlist firewall, `vibe` for opt-in unattended auto mode |

All feature paths are prefixed with `ghcr.io/jesserobertson/devcontainers`.

## Using features in a project

A project only needs a `.devcontainer/devcontainer.json`. A `pixi.toml` is optional — if none exists the feature provides a sensible default.

```
my-project/
  pixi.toml              ← optional: customise or extend the feature's default
  .devcontainer/
    devcontainer.json
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

### How it works

1. DevPod pulls the base image (cached after first pull)
2. Your project is mounted at `/workspace`
3. Each feature's `install.sh` runs — copies a default `pixi.toml` if none exists, then runs `pixi install`
4. `postCreateCommand: pixi install` reconciles any project-specific packages you've added on top
5. The fish/bash shell hook activates the pixi environment automatically on shell open

### Customising the environment

Add packages on top of the feature's defaults in your own `pixi.toml`:

```toml
[workspace]
channels = ["conda-forge"]
name = "my-project"
platforms = ["linux-64"]
version = "0.1.0"

[dependencies]
python = ">=3.11,<3.13"
fastapi = ">=0.110"
# ... the feature's packages, plus your own:
sqlalchemy = ">=2.0"
```

## Local LLM (ollama)

Run CUDA-accelerated local models on your Windows host via Docker Desktop and connect from any devcontainer.

Runs actual [Ollama](https://ollama.com), not a llama.cpp wrapper — verified this matters:
a ramalama-wrapped-llama.cpp setup used here previously failed to load recent Gemma
releases (stale bundled llama.cpp with no way to update it independently of the whole
image), while Ollama's own more current runtime loads the exact same model files fine. See
`host-services/ollama/README.md` for the specifics.

### 1. Start the host service

```bash
cd host-services/ollama
cp .env.example .env        # edit if you want a non-default port
docker compose up -d
docker compose exec ollama ollama pull llama3.2   # or any model from ollama.com/library
```

See `host-services/ollama/README.md` for prerequisites (NVIDIA Container Toolkit) and model management commands.

### 2. Add the feature to your devcontainer

Use `base-cuda` if you also want the `transformers` feature for Python-side inference. `base-ubuntu` is sufficient for the `ollama` client alone.

```json
{
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {},
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {},
    "ghcr.io/jesserobertson/devcontainers/ollama:latest": {
      "port": "11435",
      "model": "llama3.2",
      "contextSize": "8192"
    }
  },
  "runArgs": ["--gpus", "all"]
}
```

Inside the container, `OPENAI_BASE_URL` and `OLLAMA_MODEL` are set automatically. Use the `openai` client to talk to Ollama:

```python
import os
from openai import OpenAI

client = OpenAI()  # picks up OPENAI_BASE_URL and OPENAI_API_KEY from env
response = client.chat.completions.create(
    model=os.environ["OLLAMA_MODEL"],
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## Contained auto mode (agent)

The `agent` feature installs Claude Code, [Pi](https://pi.dev), and
[oh-my-pi](https://omp.sh) (`omp`, a Pi fork/superset with LSP/DAP/subagents) — all usable
against the same Anthropic account (built-in provider, reads `ANTHROPIC_API_KEY`) or a
local model (see [Local LLM (ollama)](#local-llm-ollama) above) — plus an egress-allowlist
firewall, so unattended sessions have the container itself — not model judgment — as the
safety boundary. See
[Anthropic's containment writeup](https://www.anthropic.com/engineering/how-we-contain-claude)
for the reasoning.

```json
{
  "features": {
    "ghcr.io/jesserobertson/devcontainers/agent:latest": {}
  },
  "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
  "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
  "waitFor": "postStartCommand",
  "remoteUser": "dev"
}
```

- `claude` / `pi` / `omp` — normal supervised use, with approval prompts, same as anywhere
  else. Plain `pi` has no built-in unattended mode, so it's supervised-only.
- `vibe` — the opt-in unattended entrypoint. Defaults to `omp --yolo`; `vibe claude` runs
  `claude --dangerously-skip-permissions` instead. Refuses to start unless the egress
  firewall actually armed on container start.
- Network egress is default-DROP, allowlisting only GitHub, `api.anthropic.com`,
  `claude.ai`, `pi.dev`, `omp.sh`, and the PyPI/conda-forge package hosts.
- `dev` has no sudo access anywhere in this image except one scoped rule this feature
  adds for itself: `/usr/local/bin/init-firewall.sh`, nothing else.

**Residual gap:** the firewall allows UDP/53 to any destination (not just the Docker
embedded resolver) and the Docker host's local `/24` subnet, so it narrows egress rather
than fully sealing it — a compromised agent could in principle exfiltrate data via DNS
queries to an attacker-controlled nameserver, or reach other containers on the same
bridge network. This is inherited from Anthropic's reference firewall script, not a
regression introduced here.

## Adding a new feature

1. Create `features/<name>/devcontainer-feature.json` and `features/<name>/install.sh`
2. Push to `main` — the publish workflow publishes `ghcr.io/jesserobertson/devcontainers/<name>:latest` automatically

## Repo structure

```
base/Dockerfile              ← ARG BASE_IMAGE; installs brew, pixi, dotfiles
features/
  rapids/                    ← ML: cuDF, JAX, Polars GPU, Marimo
  mojo/                      ← ML: Modular MAX / Mojo
  jax/                       ← ML: JAX (CUDA 12)
  pytorch/                   ← ML: PyTorch (CUDA 12.4)
  marimo/                    ← Data: Marimo + Altair
  fastapi/                   ← Web: FastAPI + Pydantic + Uvicorn
  cli/                       ← CLI: Typer + Rich + Pydantic
  py-devtools/               ← Dev: ruff, mypy, pytest, mkdocs
  huggingface/               ← ML: huggingface_hub, tokenizers
  transformers/              ← ML: transformers, datasets, accelerate
  ollama/                    ← ML: OpenAI-compatible Ollama client
  agent/                     ← Agent: contained claude/pi/omp (firewall + vibe auto-mode wrapper)
host-services/ollama/        ← local LLM host service (real Ollama via Docker Compose)
.github/workflows/
  build.yml                  ← builds base-ubuntu and base-cuda on Dockerfile changes
  publish-features.yml       ← publishes features via devcontainers/action on features/** changes
```
