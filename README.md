# devcontainers

Pre-built devcontainer images for GPU ML development, published to `ghcr.io/jesserobertson`. All images include fish shell, starship, neovim, pixi, and the full dotfiles setup baked in.

## Images

### Base images

| Image | From | Use for |
|-------|------|---------|
| `ghcr.io/jesserobertson/base-ubuntu:latest` | `ubuntu:24.04` | CPU-only projects |
| `ghcr.io/jesserobertson/base-cuda:latest` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | GPU projects needing a custom environment |

### Flavour images

Pre-built with a specific ML stack already installed. Start coding immediately — no `pixi install` wait on first run.

| Image | Stack |
|-------|-------|
| `ghcr.io/jesserobertson/rapids:latest` | cuDF, JAX (CUDA), Polars GPU, Marimo |
| `ghcr.io/jesserobertson/mojo:latest` | Modular MAX / Mojo (nightly) |
| `ghcr.io/jesserobertson/jax:latest` | JAX (CUDA 12), Marimo |
| `ghcr.io/jesserobertson/pytorch:latest` | PyTorch (CUDA 12), Torchvision, Marimo |

## Using a flavour image in a project

A project needs only a `pixi.toml` and a `.devcontainer/devcontainer.json`. No project-level Dockerfile required.

```
my-project/
  pixi.toml              ← optional: add project-specific packages on top
  .devcontainer/
    devcontainer.json
```

**`.devcontainer/devcontainer.json`:**

```json
{
  "name": "my-project",
  "image": "ghcr.io/jesserobertson/rapids:latest",
  "workspaceFolder": "/workspace",
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=my-project-pixi-cache,target=/root/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install",
  "remoteUser": "root"
}
```

> **Note:** only mount the pixi *cache* as a volume, not `/opt/pixi-envs`. The flavour image has the ML environment pre-installed at `/opt/pixi-envs`; mounting a volume there would shadow it.

### How it works

1. DevPod pulls the image (already cached after first pull — the heavy install is done)
2. Your project is mounted at `/workspace`
3. `pixi install` runs — this is a fast no-op if your `pixi.toml` matches the image's, or installs the delta if you've added packages
4. The fish/bash shell hook activates the pixi environment automatically on open

### Customising the environment

If you need packages beyond what the flavour provides, add them to your project's `pixi.toml`:

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
# ... inherit the flavour's packages, plus yours:
numpy = "*"
```

## Adding a new flavour

1. Create `flavours/<name>/pixi.toml` with the environment definition
2. Add `<name>` to the matrix in `.github/workflows/build.yml`
3. Push to `main` — the workflow builds and pushes `ghcr.io/jesserobertson/<name>:latest`

## Repo structure

```
base/Dockerfile          ← ARG BASE_IMAGE; installs brew, pixi, dotfiles
flavour/Dockerfile       ← shared flavour template: FROM base-cuda + pixi install
flavours/
  rapids/pixi.toml
  mojo/pixi.toml
  jax/pixi.toml
  pytorch/pixi.toml
.github/workflows/
  build.yml              ← build-bases (matrix) → build-flavours (matrix)
```
