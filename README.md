# devcontainers

Base images and composable devcontainer features for GPU ML development, published to `ghcr.io/jesserobertson`. All images include fish shell, starship, neovim, pixi, and the full dotfiles setup baked in.

## Base images

| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects (rapids, jax, mojo, pytorch) |

## Features

Composable ML environment features that install on top of a base image at container creation time. Mix and match as needed.

| Feature | Stack |
|---------|-------|
| `ghcr.io/jesserobertson/devcontainer-features/rapids:latest` | cuDF, JAX (CUDA 12), Polars GPU, Marimo |
| `ghcr.io/jesserobertson/devcontainer-features/mojo:latest` | Modular MAX / Mojo (nightly) |
| `ghcr.io/jesserobertson/devcontainer-features/jax:latest` | JAX (CUDA 12), Marimo |
| `ghcr.io/jesserobertson/devcontainer-features/pytorch:latest` | PyTorch (CUDA 12.4), Torchvision, Marimo |

## Using a feature in a project

A project needs only a `.devcontainer/devcontainer.json`. A `pixi.toml` is optional — if none exists, the feature provides a sensible default.

```
my-project/
  pixi.toml              ← optional: customise or extend the feature's default
  .devcontainer/
    devcontainer.json
```

**`.devcontainer/devcontainer.json`:**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainer-features/rapids:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=my-project-pixi-cache,target=/root/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "root"
}
```

Features are composable — combine them freely:

```json
"features": {
  "ghcr.io/jesserobertson/devcontainer-features/jax:latest": {},
  "ghcr.io/jesserobertson/devcontainer-features/pytorch:latest": {}
}
```

### How it works

1. DevPod pulls the base image (cached after first pull)
2. Your project is mounted at `/workspace`
3. The feature's `install.sh` runs — copies a default `pixi.toml` if none exists, then runs `pixi install`
4. `postCreateCommand: pixi install` reconciles any project-specific packages you've added
5. The fish/bash shell hook activates the pixi environment automatically on shell open

### Customising the environment

Add packages on top of the feature's defaults by including your own `pixi.toml`:

```toml
[workspace]
channels = ["conda-forge", "rapidsai"]
name = "my-project"
platforms = ["linux-64"]
version = "0.1.0"

[system-requirements]
cuda = "12.8"

[dependencies]
python = ">=3.11,<3.13"
numpy = "*"
# ... the feature's packages, plus your own
```

## Adding a new feature

1. Create `features/<name>/devcontainer-feature.json` and `features/<name>/install.sh`
2. Push to `main` — the publish workflow publishes `ghcr.io/jesserobertson/devcontainer-features/<name>:latest`

## Repo structure

```
base/Dockerfile              ← ARG BASE_IMAGE; installs brew, pixi, dotfiles
features/
  rapids/
    devcontainer-feature.json
    install.sh               ← copies default pixi.toml + runs pixi install
  mojo/...
  jax/...
  pytorch/...
.github/workflows/
  build.yml                  ← builds base-ubuntu and base-cuda on Dockerfile changes
  publish-features.yml       ← publishes features via devcontainers/action on features/** changes
```
