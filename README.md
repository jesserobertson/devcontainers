# devcontainers

Base images and composable devcontainer features for Python development, published to `ghcr.io/jesserobertson`. All images include fish shell, starship, neovim, pixi, and the full dotfiles setup baked in.

## Base images

| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |

## Features

Composable features that install on top of a base image at container creation time. Combine freely.

### ML / GPU

| Feature | Stack |
|---------|-------|
| `ghcr.io/jesserobertson/devcontainer-features/rapids:latest` | cuDF, JAX (CUDA 12), Polars GPU, Marimo |
| `ghcr.io/jesserobertson/devcontainer-features/mojo:latest` | Modular MAX / Mojo (nightly) |
| `ghcr.io/jesserobertson/devcontainer-features/jax:latest` | JAX (CUDA 12), Marimo |
| `ghcr.io/jesserobertson/devcontainer-features/pytorch:latest` | PyTorch (CUDA 12.4), Torchvision, Marimo |

### Web / data

| Feature | Stack |
|---------|-------|
| `ghcr.io/jesserobertson/devcontainer-features/marimo:latest` | Marimo notebook server, Altair, vega_datasets |
| `ghcr.io/jesserobertson/devcontainer-features/fastapi:latest` | FastAPI, Pydantic, Uvicorn, httpx |
| `ghcr.io/jesserobertson/devcontainer-features/cli:latest` | Typer, Rich, Pydantic, pydantic-settings |

### Dev tooling

| Feature | Stack |
|---------|-------|
| `ghcr.io/jesserobertson/devcontainer-features/py-devtools:latest` | ruff, mypy, pytest, pytest-cov, mkdocs, mkdocs-material, mkdocstrings |

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
    "ghcr.io/jesserobertson/devcontainer-features/rapids:latest": {},
    "ghcr.io/jesserobertson/devcontainer-features/py-devtools:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=my-project-pixi-cache,target=/root/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "root"
}
```

**CPU project (e.g. FastAPI service):**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainer-features/fastapi:latest": {},
    "ghcr.io/jesserobertson/devcontainer-features/py-devtools:latest": {}
  },
  "mounts": [
    "source=my-project-pixi-cache,target=/root/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "root"
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

## Adding a new feature

1. Create `features/<name>/devcontainer-feature.json` and `features/<name>/install.sh`
2. Push to `main` — the publish workflow publishes `ghcr.io/jesserobertson/devcontainer-features/<name>:latest` automatically

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
.github/workflows/
  build.yml                  ← builds base-ubuntu and base-cuda on Dockerfile changes
  publish-features.yml       ← publishes features via devcontainers/action on features/** changes
```
