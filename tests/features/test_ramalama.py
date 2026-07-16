"""Docker tests for the ramalama feature's install.sh env-var generation.

Spins up a plain ubuntu:24.04 container with a mock pixi (so no packages
are actually downloaded) and verifies that install.sh writes the correct
values to /etc/profile.d/ramalama.sh.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "ramalama"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def container_defaults():
    """Container with install.sh run using all default option values."""
    cid = _start_container()
    try:
        _prepare(cid)
        subprocess.run(
            ["docker", "exec", cid, "bash", "/tmp/install.sh"],
            check=True,
        )
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


@pytest.fixture(scope="module")
def container_custom():
    """Container with install.sh run using custom option values (as devcontainer would pass them)."""
    cid = _start_container()
    try:
        _prepare(cid)
        subprocess.run(
            [
                "docker", "exec",
                "-e", "HOST=ramalama",
                "-e", "PORT=9090",
                "-e", "MODEL=huggingface://microsoft/Phi-3-mini-4k-instruct",
                "-e", "APIKEY=sk-test",
                "-e", "CONTEXTSIZE=8192",
                cid, "bash", "/tmp/install.sh",
            ],
            check=True,
        )
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


# ---------------------------------------------------------------------------
# Default option values
# ---------------------------------------------------------------------------

def test_profile_script_exists(container_defaults):
    result = subprocess.run(
        ["docker", "exec", container_defaults, "test", "-f", "/etc/profile.d/ramalama.sh"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_profile_script_permissions(container_defaults):
    assert _exec(container_defaults, "stat -c '%a' /etc/profile.d/ramalama.sh") == "644"


def test_openai_base_url_default(container_defaults):
    val = _sourced(container_defaults, "OPENAI_BASE_URL")
    assert val == "http://host.docker.internal:8080/v1"


def test_ramalama_host_default(container_defaults):
    assert _sourced(container_defaults, "RAMALAMA_HOST") == "host.docker.internal"


def test_ramalama_port_default(container_defaults):
    assert _sourced(container_defaults, "RAMALAMA_PORT") == "8080"


def test_ramalama_model_default(container_defaults):
    assert _sourced(container_defaults, "RAMALAMA_MODEL") == "ollama://llama3.2"


def test_openai_api_key_default(container_defaults):
    assert _sourced(container_defaults, "OPENAI_API_KEY") == "ramalama"


def test_context_size_default(container_defaults):
    assert _sourced(container_defaults, "RAMALAMA_CONTEXT_SIZE") == "4096"


# ---------------------------------------------------------------------------
# Custom option values
# ---------------------------------------------------------------------------

def test_openai_base_url_custom(container_custom):
    assert _sourced(container_custom, "OPENAI_BASE_URL") == "http://ramalama:9090/v1"


def test_ramalama_model_custom(container_custom):
    assert _sourced(container_custom, "RAMALAMA_MODEL") == "huggingface://microsoft/Phi-3-mini-4k-instruct"


def test_context_size_custom(container_custom):
    assert _sourced(container_custom, "RAMALAMA_CONTEXT_SIZE") == "8192"


def test_openai_api_key_custom(container_custom):
    assert _sourced(container_custom, "OPENAI_API_KEY") == "sk-test"


def test_ramalama_host_custom(container_custom):
    assert _sourced(container_custom, "RAMALAMA_HOST") == "ramalama"


def test_ramalama_port_custom(container_custom):
    assert _sourced(container_custom, "RAMALAMA_PORT") == "9090"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_container() -> str:
    return subprocess.check_output(
        ["docker", "run", "-d", "ubuntu:24.04", "sleep", "infinity"],
        text=True,
    ).strip()


def _prepare(cid: str) -> None:
    """Create the dev user, install a mock pixi at the path install.sh actually
    invokes (/home/dev/.local/share/pixi/bin/pixi, fully-qualified since `su dev -c`
    doesn't inherit PATH — and PIXI_HOME, set explicitly in base/Dockerfile, is
    where pixi actually installs to), and copy install.sh into the container."""
    subprocess.run(
        ["docker", "exec", cid, "bash", "-c",
         "useradd -m -s /bin/bash dev && "
         "mkdir -p /etc/profile.d /home/dev/.local/share/pixi/bin && "
         "printf '#!/bin/bash\\necho pixi: $@\\n' > /home/dev/.local/share/pixi/bin/pixi && "
         "chmod +x /home/dev/.local/share/pixi/bin/pixi"],
        check=True,
    )
    subprocess.run(
        ["docker", "cp", str(FEATURE_DIR / "install.sh"), f"{cid}:/tmp/install.sh"],
        check=True,
    )


def _exec(cid: str, cmd: str) -> str:
    return subprocess.check_output(
        ["docker", "exec", cid, "bash", "-c", cmd], text=True
    ).strip()


def _sourced(cid: str, var: str) -> str:
    return _exec(cid, f"source /etc/profile.d/ramalama.sh && echo ${var}")
