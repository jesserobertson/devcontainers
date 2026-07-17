"""Integration tests for the ollama-sidecar example.

CPU tests (always run):
  - Validate docker compose config is syntactically correct
  - Verify expected services are declared

GPU tests (local only, pytest --gpu):
  - Start the ollama sidecar, wait for it to serve /v1/models
"""

import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "examples" / "ollama-sidecar" / ".devcontainer" / "docker-compose.yml"


# ---------------------------------------------------------------------------
# CPU — always run
# ---------------------------------------------------------------------------

def test_compose_config_valid():
    """docker compose config parses the file without errors."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_compose_services_defined():
    """Both app and ollama services are declared."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--services"],
        capture_output=True,
        text=True,
        check=True,
    )
    services = result.stdout.strip().splitlines()
    assert "app" in services
    assert "ollama" in services


# ---------------------------------------------------------------------------
# GPU — local only (pytest --gpu)
# ---------------------------------------------------------------------------

@pytest.mark.gpu
def test_ollama_sidecar_serves():
    """ollama sidecar starts and exposes an OpenAI-compatible /v1/models endpoint,
    reachable from the app service via the internal compose network.

    The sidecar's ollama service has no host port mapping by design (it's only
    meant to be reached from the app container, over the internal compose
    network, at the service name "ollama" — matching exactly how the ollama
    feature's "host": "ollama" option resolves it). So this test execs into
    the app container and curls http://ollama:11434 from there, rather than
    querying a host-mapped port — querying localhost:PORT from the test
    runner would silently pass against an unrelated process on that port
    (verified this happens in practice: a native Ollama install, or the
    separate host-services/ollama service, can both be listening on a
    similar port from the host's point of view without this sidecar being
    reachable at all).

    Unlike the old ramalama-based sidecar, Ollama's server listens immediately on
    startup regardless of whether any model has been pulled yet (`/v1/models` just
    returns an empty list until you `ollama pull` something) — so this only checks
    the server itself comes up, not that a specific model is loaded.
    """
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "app", "ollama"],
            check=True,
        )
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            result = subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "app",
                 "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "http://ollama:11434/v1/models"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip() == "200":
                ready = True
                break
            time.sleep(2)
        assert ready, "ollama (reached via the app container's internal network) did not become ready within 60 seconds"
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            capture_output=True,
        )
