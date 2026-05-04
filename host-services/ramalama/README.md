# ramalama host service

Runs ramalama as a persistent CUDA-accelerated service on the host via Docker Desktop.
Devcontainers reach it at `host.docker.internal:8080` (Docker Desktop maps this automatically on Windows).

## Prerequisites

- Docker Desktop for Windows with the WSL2 backend enabled
- NVIDIA GPU with drivers installed on the host
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) configured for Docker Desktop

## Setup

```bash
# 1. Copy and configure the env file
cp .env.example .env
# Edit .env to set RAMALAMA_MODEL to your preferred model

# 2. Start the service
docker compose up -d

# 3. Verify it is running
docker compose logs -f ramalama
# Look for: "Server listening on port 8080"
```

## Managing models

```bash
# Pull a model (e.g. a Phi-3 mini from HuggingFace)
docker compose exec ramalama ramalama pull huggingface://microsoft/Phi-3-mini-4k-instruct

# List cached models
docker compose exec ramalama ramalama list

# Swap the default model without rebuilding
RAMALAMA_MODEL=huggingface://microsoft/Phi-3-mini-4k-instruct docker compose up -d
```

## Stopping

```bash
docker compose down       # stop, keep model cache volume
docker compose down -v    # stop and delete model cache (re-download required)
```

## Pointing your devcontainer at a different host or port

In your project's `devcontainer.json`:

```json
"ghcr.io/jesserobertson/devcontainers/ramalama:latest": {
  "host": "host.docker.internal",
  "port": "9090"
}
```
