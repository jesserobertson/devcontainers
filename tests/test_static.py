"""Static validation: JSON structure, bash syntax, YAML — no Docker required."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
FEATURES = ["huggingface", "transformers", "ramalama"]


# --- per-feature parametrised checks ---

@pytest.mark.parametrize("feature", FEATURES)
def test_feature_json_has_required_fields(feature):
    data = _feature_json(feature)
    for field in ("id", "version", "name", "description"):
        assert field in data, f"missing field '{field}' in {feature}"


@pytest.mark.parametrize("feature", FEATURES)
def test_feature_json_id_matches_dir(feature):
    assert _feature_json(feature)["id"] == feature


@pytest.mark.parametrize("feature", FEATURES)
def test_install_sh_syntax(feature):
    script = REPO_ROOT / "features" / feature / "install.sh"
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# --- huggingface ---

def test_huggingface_hf_home_containerenv():
    assert _feature_json("huggingface")["containerEnv"]["HF_HOME"] == "/workspace/.cache/huggingface"


def test_huggingface_no_options():
    assert _feature_json("huggingface").get("options", {}) == {}


# --- transformers ---

def test_transformers_no_container_env():
    assert "containerEnv" not in _feature_json("transformers")


def test_transformers_no_options():
    assert _feature_json("transformers").get("options", {}) == {}


# --- ramalama ---

def test_ramalama_has_five_options():
    assert set(_feature_json("ramalama")["options"]) == {"host", "port", "model", "apiKey", "contextSize"}


@pytest.mark.parametrize("option,expected", [
    ("host",        "host.docker.internal"),
    ("port",        "8080"),
    ("model",       "ollama://llama3.2"),
    ("apiKey",      "ramalama"),
    ("contextSize", "4096"),
])
def test_ramalama_option_default(option, expected):
    assert _feature_json("ramalama")["options"][option]["default"] == expected


def test_ramalama_no_container_env():
    assert "containerEnv" not in _feature_json("ramalama")


# --- compose YAML ---

@pytest.mark.parametrize("rel_path", [
    "examples/ramalama-sidecar/.devcontainer/docker-compose.yml",
    "host-services/ramalama/docker-compose.yml",
])
def test_compose_valid_yaml(rel_path):
    data = _yaml(rel_path)
    assert "services" in data


def test_example_sidecar_has_app_and_ramalama():
    data = _yaml("examples/ramalama-sidecar/.devcontainer/docker-compose.yml")
    assert "app" in data["services"]
    assert "ramalama" in data["services"]


def test_example_sidecar_ramalama_image():
    data = _yaml("examples/ramalama-sidecar/.devcontainer/docker-compose.yml")
    assert data["services"]["ramalama"]["image"] == "ghcr.io/jesserobertson/ramalama:latest"


def test_example_sidecar_gpu_config():
    data = _yaml("examples/ramalama-sidecar/.devcontainer/docker-compose.yml")
    devices = data["services"]["ramalama"]["deploy"]["resources"]["reservations"]["devices"]
    assert any(d.get("driver") == "nvidia" for d in devices)


def test_host_services_model_volume():
    data = _yaml("host-services/ramalama/docker-compose.yml")
    volumes = data["services"]["ramalama"].get("volumes", [])
    assert any("/root/.local/share/ramalama" in str(v) for v in volumes)


# --- helpers ---

def _feature_json(feature: str) -> dict:
    path = REPO_ROOT / "features" / feature / "devcontainer-feature.json"
    return json.loads(path.read_text())


def _yaml(rel_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / rel_path).read_text())
