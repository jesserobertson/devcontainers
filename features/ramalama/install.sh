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

chmod 644 /etc/profile.d/ramalama.sh
