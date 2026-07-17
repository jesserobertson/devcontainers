#!/bin/bash
set -e

HOST="${HOST:-host.docker.internal}"
PORT="${PORT:-11434}"
MODEL="${MODEL:-llama3.2}"
APIKEY="${APIKEY:-ollama}"
CONTEXTSIZE="${CONTEXTSIZE:-4096}"

su dev -c '/home/dev/.local/share/pixi/bin/pixi global install --environment dev --channel conda-forge openai'

cat > /etc/profile.d/ollama.sh <<EOF
export OLLAMA_HOST="${HOST}"
export OLLAMA_PORT="${PORT}"
export OLLAMA_MODEL="${MODEL}"
export OPENAI_BASE_URL="http://${HOST}:${PORT}/v1"
export OPENAI_API_KEY="${APIKEY}"
export OLLAMA_CONTEXT_SIZE="${CONTEXTSIZE}"
EOF

chmod 644 /etc/profile.d/ollama.sh
