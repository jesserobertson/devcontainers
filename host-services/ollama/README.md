# ollama host service

Runs [Ollama](https://ollama.com) as a persistent CUDA-accelerated service on the host via
Docker Desktop. Devcontainers reach it at `host.docker.internal:11434` (Docker Desktop maps
this automatically on Windows).

!!! Previously this ran `ramalama` (a llama.cpp wrapper). Switched to Ollama directly after
finding ramalama's bundled llama.cpp (as of the `quay.io/ramalama/cuda:latest` image built
2026-06-24) fails to load recent Gemma releases (`error loading model: done_getting_tensors:
wrong number of tensors` for Gemma 4, `key not found: gemma3.attention.layer_norm_rms_epsilon`
for Gemma 3) — verified the *exact same* model blob (same sha256) loads and serves correctly
through Ollama's own more current runtime. Gemma 2 and Llama 3.2 worked fine under the old
ramalama setup too, so this wasn't a universal problem — just recent Gemma releases outrunning
ramalama's bundled llama.cpp version, with no way to pin a newer one (ramalama always uses
whatever's baked into the pulled image).

## Prerequisites

- Docker Desktop for Windows with the WSL2 backend enabled
- NVIDIA GPU with drivers installed on the host
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) configured for Docker Desktop

## Setup

```bash
# 1. Copy and configure the env file
cp .env.example .env
# Edit .env if you want a non-default port

# 2. Start the service
docker compose up -d

# 3. Verify it is running
docker compose logs -f ollama
# Look for: "Listening on [::]:11434"

# 4. Pull a model
docker compose exec ollama ollama pull gemma4:e2b
```

## Managing models

```bash
# Pull a model (any tag from https://ollama.com/library)
docker compose exec ollama ollama pull llama3.2

# List cached models
docker compose exec ollama ollama list

# Remove a model
docker compose exec ollama ollama rm gemma4:e2b
```

Models this was actually verified against on an 8GB-VRAM RTX 4070 Laptop GPU:

| Model | Result |
|---|---|
| `llama3.2` | Works |
| `gemma2:2b` | Works |
| `gemma4:e2b` | Works (via Ollama; failed under the old ramalama/llama.cpp setup) |
| `gemma3:4b` | Not retried under Ollama, but expected to work — it's the same class of failure Gemma 4 had under ramalama |

## Stopping

```bash
docker compose down       # stop, keep model cache volume
docker compose down -v    # stop and delete model cache (re-download required)
```

## Pointing your devcontainer at a different host or port

In your project's `devcontainer.json`:

```json
"ghcr.io/jesserobertson/devcontainers/ollama:latest": {
  "host": "host.docker.internal",
  "port": "9090"
}
```
