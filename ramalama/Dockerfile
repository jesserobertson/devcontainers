FROM ghcr.io/jesserobertson/base-cuda:latest

# This sidecar is an inference server, not an agent-execution context, so
# it intentionally stays root rather than picking up base-cuda's non-root
# default user.
USER root

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y python3-pip

# llama-cpp-python CUDA 12.4 wheels are compatible with CUDA 12.8
RUN pip install --break-system-packages ramalama \
    "llama-cpp-python[server]" \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

ENV RAMALAMA_MODEL=ollama://llama3.2
ENV RAMALAMA_PORT=8080

EXPOSE 8080

CMD ramalama serve --nocontainer ${RAMALAMA_MODEL} --port ${RAMALAMA_PORT}
