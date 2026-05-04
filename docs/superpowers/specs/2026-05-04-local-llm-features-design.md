# Local LLM Features Design

**Date:** 2026-05-04
**Status:** Approved

## Overview

Three new composable devcontainer features to support local LLM inference via ramalama and HuggingFace tooling. Motivated by a project blending Obsidian with local model inference (ramalama). The ramalama service runs as a separate host-side Docker container (managed by Docker Desktop on Windows); the devcontainer features handle client-side setup only.

## Features

### `features/huggingface/`

CPU-safe. Works on `base-ubuntu` or `base-cuda`.

**Installs (via pixi, conda-forge):**
- `huggingface_hub` — model download, upload, cache management
- `tokenizers` — fast tokenizer library (useful standalone, e.g. for embeddings)

**Environment variable set via `containerEnv`:**
- `HF_HOME=/workspace/.cache/huggingface` — keeps model downloads workspace-local; survives container rebuilds when workspace is mounted as a volume

**devcontainer-feature.json options:** none (intentionally minimal)

---

### `features/transformers/`

CUDA-recommended (`base-cuda`). Works CPU-only on `base-ubuntu` but inference will be slow.

**Installs (via pixi, conda-forge):**
- `transformers`
- `datasets`
- `accelerate`
- `tokenizers`

Does not bundle `huggingface_hub` — add the `huggingface` feature alongside if you need the hub CLI / `HF_HOME` configuration. This keeps the dependency graph explicit and composable.

**devcontainer-feature.json options:** none

---

### `features/ramalama/`

Client-side only. Works on either base image. Assumes a ramalama service is already running on the host (see host-services setup below).

**Installs (via pixi, conda-forge):**
- `openai` — OpenAI-compatible REST client for talking to the ramalama service

**Options and env vars** (written from `install.sh` to both `/etc/profile.d/ramalama.sh` for bash and `/root/.config/fish/conf.d/ramalama.fish` for fish — the base image defaults to fish shell):

| Option | Default | Env var(s) written | Notes |
|--------|---------|-------------------|-------|
| `host` | `host.docker.internal` | `RAMALAMA_HOST`, `OPENAI_BASE_URL` | Docker Desktop on Windows maps `host.docker.internal` to the host automatically |
| `port` | `8080` | `RAMALAMA_PORT`, `OPENAI_BASE_URL` | ramalama default serve port |
| `model` | `ollama://llama3.2` | `RAMALAMA_MODEL` | ramalama model identifier format: `ollama://…`, `huggingface://…`, `oci://…` |
| `apiKey` | `ramalama` | `OPENAI_API_KEY` | ramalama doesn't require a real key; override to point at a real OpenAI-compatible API |
| `contextSize` | `4096` | `RAMALAMA_CONTEXT_SIZE` | Hint for client code about expected context window size |

`OPENAI_BASE_URL` is composed as `http://${host}:${port}/v1` so changing host or port automatically updates the URL.

---

## Host-Side Service Setup

### `host-services/ramalama/`

A `docker-compose.yml` + `README.md` for running ramalama as a persistent host service via Docker Desktop (Windows). Not a devcontainer feature — this is run once on the host.

**`docker-compose.yml`** will:
- Use the official `quay.io/ramalama/cuda` image (versioned to ramalama minor release)
- Pass `--gpus all` for CUDA acceleration
- Expose port 8080
- Mount a named volume for model cache (so models persist across ramalama container restarts)
- Run `ramalama serve ${RAMALAMA_MODEL}` as the command (model configurable via `.env`)

**`README.md`** covers:
- Prerequisites: Docker Desktop with NVIDIA Container Toolkit on Windows
- How to start/stop the service (`docker compose up -d` / `docker compose down`)
- How to pull models (`docker compose exec ramalama ramalama pull <model>`)
- How to point the devcontainer feature at a different host/port if needed

---

## Example Usage

**`devcontainer.json` for an Obsidian/local-LLM project:**

```json
{
  "name": "obsidian-llm",
  "image": "ghcr.io/jesserobertson/base-cuda:latest",
  "workspaceFolder": "/workspace",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {},
    "ghcr.io/jesserobertson/devcontainers/transformers:latest": {},
    "ghcr.io/jesserobertson/devcontainers/ramalama:latest": {
      "model": "huggingface://microsoft/Phi-3-mini-4k-instruct",
      "contextSize": "8192"
    },
    "ghcr.io/jesserobertson/devcontainers/py-devtools:latest": {}
  },
  "runArgs": ["--gpus", "all"],
  "mounts": [
    "source=obsidian-llm-pixi-cache,target=/root/.cache/pixi,type=volume"
  ],
  "postCreateCommand": "pixi install"
}
```

**CPU-only project using only HuggingFace hub:**

```json
{
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "features": {
    "ghcr.io/jesserobertson/devcontainers/huggingface:latest": {}
  }
}
```

---

## README Updates

Add three rows to the features table:

| Feature | Use | Stack | Description |
|---------|-----|-------|-------------|
| `…/huggingface:latest` | ML | base-ubuntu / base-cuda | HuggingFace tooling — huggingface_hub, tokenizers |
| `…/transformers:latest` | ML | base-cuda | HuggingFace inference stack — transformers, datasets, accelerate |
| `…/ramalama:latest` | ML | base-ubuntu / base-cuda | Local LLM client — OpenAI-compatible client for ramalama service |

Add a "Local LLM" section to the README explaining the host-services setup and the ramalama feature.

---

## Out of Scope

- Installing ramalama CLI inside the devcontainer (it spawns containers, which conflicts with the devcontainer environment)
- `peft` / `trl` / fine-tuning tools (can be added to a project's own `pixi.toml` as needed)
- Diffusers (separate feature if needed later)
- Server-side ramalama config (runtime, threads, GPU layers) — configured on the host service side
