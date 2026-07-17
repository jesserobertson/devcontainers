"""Integration tests for the ollama-sidecar example.

CPU tests (always run):
  - Validate docker compose config is syntactically correct
  - Verify expected services are declared

GPU tests (local only, pytest --gpu):
  - Start the ollama sidecar, wait for it to serve /v1/models
"""

import subprocess
import time
import urllib.error
import urllib.request
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
    """ollama sidecar starts and exposes an OpenAI-compatible /v1/models endpoint.

    Unlike the old ramalama-based sidecar, Ollama's server listens immediately on
    startup regardless of whether any model has been pulled yet (`/v1/models` just
    returns an empty list until you `ollama pull` something) — so this only checks
    the server itself comes up, not that a specific model is loaded.
    """
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "ollama"],
            check=True,
        )
        deadline = time.time() + 60
        ready = False
        while time.time() < deadline:
            try:
                r = urllib.request.urlopen("http://localhost:11434/v1/models", timeout=3)
                if r.status == 200:
                    ready = True
                    break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        assert ready, "ollama did not become ready within 60 seconds"
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            capture_output=True,
        )
