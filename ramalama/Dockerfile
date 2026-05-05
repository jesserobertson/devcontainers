FROM ghcr.io/jesserobertson/base-cuda:latest

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
