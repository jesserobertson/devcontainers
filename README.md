# devcontainers

Base images and composable devcontainer features for Python development, published to `ghcr.io/jesserobertson`. All images include fish shell, starship, neovim, and pixi — with the full dotfiles setup from [jesserobertson/dotfiles](https://github.com/jesserobertson/dotfiles) baked in.

## Base images

| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |
| `ghcr.io/jesserobertson/ramalama:latest` | `base-cuda` | Local LLM sidecar — ramalama + llama.cpp with CUDA |

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
| `…/ramalama:latest` | ML | base-ubuntu / base-cuda | Local LLM client — OpenAI-compatible client for a ramalama service |

All feature paths are prefixed with `ghcr.io/jesserobertson/devcontainers`.

## Using features in a project

A project only needs a `.devcontainer/devcontainer.json`. A `pixi.toml` is optional — if none exists the feature provides a sensible default.

```
my-project/
  pixi.toml              ← optional: customise or extend the feature's default
  .devcontainer/
    devcontainer.json
```

**GPU project (e.g. rapids):**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
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

## Local LLM (ramalama)

Run CUDA-accelerated local models on your Windows host via Docker Desktop and connect from any devcontainer.

### 1. Start the host service

```bash
cd host-services/ramalama
cp .env.example .env        # edit RAMALAMA_MODEL if desired
docker compose up -d
```

See `host-services/ramalama/README.md` for prerequisites (NVIDIA Container Toolkit) and model management commands.

### 2. Add the feature to your devcontainer

Use `base-cuda` if you also want the `transformers` feature for Python-side inference. `base-ubuntu` is sufficient for the `ramalama` client alone.

```json
{
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {},
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {},
    "ghcr.io/jesserobertson/devcontainers/ramalama:latest": {
      "model": "ollama://llama3.2",
      "contextSize": "8192"
    }
  },
  "runArgs": ["--gpus", "all"]
}
```

Inside the container, `OPENAI_BASE_URL` and `RAMALAMA_MODEL` are set automatically. Use the `openai` client to talk to ramalama:

```python
import os
from openai import OpenAI

client = OpenAI()  # picks up OPENAI_BASE_URL and OPENAI_API_KEY from env
response = client.chat.completions.create(
    model=os.environ["RAMALAMA_MODEL"],
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## Adding a new feature

1. Create `features/<name>/devcontainer-feature.json` and `features/<name>/install.sh`
2. Push to `main` — the publish workflow publishes `ghcr.io/jesserobertson/devcontainers/<name>:latest` automatically

## Repo structure

```
base/Dockerfile              ← ARG BASE_IMAGE; installs brew, pixi, dotfiles
ramalama/Dockerfile          ← local LLM sidecar image (FROM base-cuda + ramalama + llama.cpp)
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
  ramalama/                  ← ML: OpenAI-compatible ramalama client
.github/workflows/
  build.yml                  ← builds base-ubuntu and base-cuda on Dockerfile changes
  build-ramalama.yml         ← builds ramalama image on ramalama/Dockerfile changes
  publish-features.yml       ← publishes features via devcontainers/action on features/** changes
```
