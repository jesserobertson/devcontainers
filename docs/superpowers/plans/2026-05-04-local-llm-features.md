# Local LLM Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three composable devcontainer features (`huggingface`, `transformers`, `ramalama`) plus a host-side ramalama service definition for running local CUDA-accelerated LLMs from a Windows + Docker Desktop host.

**Architecture:** Each feature follows the existing pattern — a `devcontainer-feature.json` for metadata and a `install.sh` that calls `pixi global install --environment dev`. The `ramalama` feature is client-side only: it installs the `openai` Python package and writes connection env vars (host, port, model, etc.) to `/etc/profile.d/ramalama.sh`. The ramalama server itself runs as a Docker Compose service on the host, reachable from the devcontainer at `host.docker.internal:8080`.

**Tech Stack:** pixi (conda-forge packages), devcontainer features spec, Docker Compose, ramalama (`quay.io/ramalama/cuda`), NVIDIA Container Toolkit

---

### Task 1: `features/huggingface/` — HuggingFace Hub feature

**Files:**
- Create: `features/huggingface/devcontainer-feature.json`
- Create: `features/huggingface/install.sh`

- [ ] **Step 1: Validate the pattern by re-reading an existing feature**

  Read `features/marimo/devcontainer-feature.json` and `features/marimo/install.sh` to confirm the JSON shape and install pattern before writing new files.

- [ ] **Step 2: Create `features/huggingface/devcontainer-feature.json`**

  ```json
  {
    "id": "huggingface",
    "version": "1.0.0",
    "name": "HuggingFace Hub",
    "description": "Installs huggingface_hub and tokenizers via pixi. Sets HF_HOME to /workspace/.cache/huggingface. Works on base-ubuntu or base-cuda.",
    "options": {},
    "containerEnv": {
      "HF_HOME": "/workspace/.cache/huggingface"
    }
  }
  ```

- [ ] **Step 3: Create `features/huggingface/install.sh`**

  ```bash
  #!/bin/bash
  set -e

  pixi global install --environment dev --channel conda-forge \
      huggingface_hub tokenizers
  ```

- [ ] **Step 4: Validate JSON and shell syntax**

  Run:
  ```bash
  jq . features/huggingface/devcontainer-feature.json
  bash -n features/huggingface/install.sh
  ```
  Expected: `jq` prints the JSON back cleanly, `bash -n` exits with no output and code 0.

- [ ] **Step 5: Make install.sh executable and commit**

  ```bash
  chmod +x features/huggingface/install.sh
  git add features/huggingface/
  git commit -m "feat: add huggingface feature (huggingface_hub, tokenizers)"
  ```

---

### Task 2: `features/transformers/` — HuggingFace inference stack

**Files:**
- Create: `features/transformers/devcontainer-feature.json`
- Create: `features/transformers/install.sh`

- [ ] **Step 1: Create `features/transformers/devcontainer-feature.json`**

  ```json
  {
    "id": "transformers",
    "version": "1.0.0",
    "name": "HuggingFace Transformers",
    "description": "Installs transformers, datasets, accelerate, and tokenizers via pixi. CUDA-recommended (base-cuda); works CPU-only on base-ubuntu. Add the huggingface feature alongside for hub CLI and HF_HOME config.",
    "options": {}
  }
  ```

- [ ] **Step 2: Create `features/transformers/install.sh`**

  ```bash
  #!/bin/bash
  set -e

  pixi global install --environment dev --channel conda-forge \
      transformers datasets accelerate tokenizers
  ```

- [ ] **Step 3: Validate JSON and shell syntax**

  Run:
  ```bash
  jq . features/transformers/devcontainer-feature.json
  bash -n features/transformers/install.sh
  ```
  Expected: both exit cleanly with no errors.

- [ ] **Step 4: Make install.sh executable and commit**

  ```bash
  chmod +x features/transformers/install.sh
  git add features/transformers/
  git commit -m "feat: add transformers feature (transformers, datasets, accelerate)"
  ```

---

### Task 3: `features/ramalama/` — ramalama client feature

**Files:**
- Create: `features/ramalama/devcontainer-feature.json`
- Create: `features/ramalama/install.sh`

- [ ] **Step 1: Create `features/ramalama/devcontainer-feature.json`**

  devcontainer features pass option values to `install.sh` as uppercase env vars (`host` → `HOST`, `apiKey` → `APIKEY`, `contextSize` → `CONTEXTSIZE`).

  ```json
  {
    "id": "ramalama",
    "version": "1.0.0",
    "name": "RamaLama client",
    "description": "Installs the OpenAI-compatible Python client and configures connection env vars for a ramalama service running on the host. Assumes ramalama is running via Docker Desktop — see host-services/ramalama/ for setup.",
    "options": {
      "host": {
        "type": "string",
        "default": "host.docker.internal",
        "description": "Hostname where the ramalama service is running. Docker Desktop maps host.docker.internal to the Windows host automatically."
      },
      "port": {
        "type": "string",
        "default": "8080",
        "description": "Port the ramalama service is listening on."
      },
      "model": {
        "type": "string",
        "default": "ollama://llama3.2",
        "description": "Default model identifier in ramalama format: ollama://name, huggingface://org/name, or oci://registry/name."
      },
      "apiKey": {
        "type": "string",
        "default": "ramalama",
        "description": "Value for OPENAI_API_KEY. ramalama does not require a real key; override only when pointing at a real OpenAI-compatible API."
      },
      "contextSize": {
        "type": "string",
        "default": "4096",
        "description": "Context window size hint written as RAMALAMA_CONTEXT_SIZE for use by client code."
      }
    }
  }
  ```

- [ ] **Step 2: Create `features/ramalama/install.sh`**

  ```bash
  #!/bin/bash
  set -e

  HOST="${HOST:-host.docker.internal}"
  PORT="${PORT:-8080}"
  MODEL="${MODEL:-ollama://llama3.2}"
  APIKEY="${APIKEY:-ramalama}"
  CONTEXTSIZE="${CONTEXTSIZE:-4096}"

  pixi global install --environment dev --channel conda-forge openai

  cat > /etc/profile.d/ramalama.sh <<EOF
  export RAMALAMA_HOST="${HOST}"
  export RAMALAMA_PORT="${PORT}"
  export RAMALAMA_MODEL="${MODEL}"
  export OPENAI_BASE_URL="http://${HOST}:${PORT}/v1"
  export OPENAI_API_KEY="${APIKEY}"
  export RAMALAMA_CONTEXT_SIZE="${CONTEXTSIZE}"
  EOF

  chmod +x /etc/profile.d/ramalama.sh
  ```

- [ ] **Step 3: Validate JSON and shell syntax**

  Run:
  ```bash
  jq . features/ramalama/devcontainer-feature.json
  bash -n features/ramalama/install.sh
  ```
  Expected: both exit cleanly with no errors.

- [ ] **Step 4: Verify the heredoc produces correct output**

  Run a quick smoke test to confirm variable interpolation is right:
  ```bash
  HOST=myhost PORT=9090 MODEL=ollama://llama3.2 APIKEY=ramalama CONTEXTSIZE=4096 \
    bash -c '
      cat <<EOF
  export RAMALAMA_HOST="${HOST}"
  export OPENAI_BASE_URL="http://${HOST}:${PORT}/v1"
  export RAMALAMA_MODEL="${MODEL}"
  EOF
    '
  ```
  Expected output:
  ```
  export RAMALAMA_HOST="myhost"
  export OPENAI_BASE_URL="http://myhost:9090/v1"
  export RAMALAMA_MODEL="ollama://llama3.2"
  ```

- [ ] **Step 5: Make install.sh executable and commit**

  ```bash
  chmod +x features/ramalama/install.sh
  git add features/ramalama/
  git commit -m "feat: add ramalama client feature (openai client, connection env vars)"
  ```

---

### Task 4: `host-services/ramalama/` — host-side Docker Compose service

**Files:**
- Create: `host-services/ramalama/docker-compose.yml`
- Create: `host-services/ramalama/.env.example`
- Create: `host-services/ramalama/README.md`

- [ ] **Step 1: Create `host-services/ramalama/docker-compose.yml`**

  ```yaml
  services:
    ramalama:
      image: quay.io/ramalama/cuda:${RAMALAMA_VERSION:-latest}
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: all
                capabilities: [gpu]
      ports:
        - "${RAMALAMA_PORT:-8080}:8080"
      volumes:
        - ramalama-models:/root/.local/share/ramalama
      command: serve ${RAMALAMA_MODEL:-ollama://llama3.2} --port 8080

  volumes:
    ramalama-models:
  ```

- [ ] **Step 2: Create `host-services/ramalama/.env.example`**

  ```
  # Copy to .env and adjust as needed
  RAMALAMA_VERSION=latest
  RAMALAMA_PORT=8080
  RAMALAMA_MODEL=ollama://llama3.2
  ```

- [ ] **Step 3: Create `host-services/ramalama/README.md`**

  ```markdown
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
  ```

- [ ] **Step 4: Validate the compose file**

  Run:
  ```bash
  docker compose -f host-services/ramalama/docker-compose.yml config
  ```
  Expected: Docker Compose prints the resolved config with no errors. (Requires Docker Desktop to be running.)

  If Docker is not available in this environment, skip to step 5 — the compose file will be validated during integration testing.

- [ ] **Step 5: Commit**

  ```bash
  git add host-services/ramalama/
  git commit -m "feat: add ramalama host service (docker compose, CUDA, model cache volume)"
  ```

---

### Task 5: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add three rows to the features table**

  Find the table that starts with `| Feature | Use | Stack | Description |`. Add these three rows after the existing `…/py-devtools:latest` row:

  ```markdown
  | `…/huggingface:latest` | ML | base-ubuntu / base-cuda | HuggingFace tooling — huggingface_hub, tokenizers; sets HF_HOME |
  | `…/transformers:latest` | ML | base-cuda | HuggingFace inference — transformers, datasets, accelerate |
  | `…/ramalama:latest` | ML | base-ubuntu / base-cuda | Local LLM client — OpenAI-compatible client for a ramalama service |
  ```

- [ ] **Step 2: Add a Local LLM section after the existing feature examples**

  Add this section after the closing of the "Customising the environment" section (before "Adding a new feature"):

  ```markdown
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
  ```

- [ ] **Step 3: Verify the README renders correctly**

  Run:
  ```bash
  python3 -c "
  with open('README.md') as f:
      content = f.read()
  assert '…/huggingface:latest' in content
  assert '…/transformers:latest' in content
  assert '…/ramalama:latest' in content
  assert 'Local LLM' in content
  assert 'host-services/ramalama' in content
  print('README checks passed')
  "
  ```
  Expected: `README checks passed`

- [ ] **Step 4: Commit**

  ```bash
  git add README.md
  git commit -m "docs: add huggingface, transformers, ramalama to README and features table"
  ```
