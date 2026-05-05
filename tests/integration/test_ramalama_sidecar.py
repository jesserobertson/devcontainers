"""Integration tests for the ramalama-sidecar example.

CPU tests (always run):
  - Validate docker compose config is syntactically correct
  - Verify expected services are declared

GPU tests (local only, pytest --gpu):
  - Start ramalama sidecar, wait for it to serve /v1/models
"""

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
COMPOSE_FILE = REPO_ROOT / "examples" / "ramalama-sidecar" / ".devcontainer" / "docker-compose.yml"


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
    """Both app and ramalama services are declared."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--services"],
        capture_output=True,
        text=True,
        check=True,
    )
    services = result.stdout.strip().splitlines()
    assert "app" in services
    assert "ramalama" in services


# ---------------------------------------------------------------------------
# GPU — local only (pytest --gpu)
# ---------------------------------------------------------------------------

@pytest.mark.gpu
def test_ramalama_sidecar_serves():
    """ramalama sidecar starts and exposes an OpenAI-compatible /v1/models endpoint."""
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "ramalama"],
            check=True,
        )
        # Poll up to 120s — model load time varies
        deadline = time.time() + 120
        ready = False
        while time.time() < deadline:
            try:
                r = urllib.request.urlopen("http://localhost:8080/v1/models", timeout=3)
                if r.status == 200:
                    ready = True
                    break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(4)
        assert ready, "ramalama did not become ready within 120 seconds"
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down"],
            capture_output=True,
        )
