"""Static validation: JSON structure, bash syntax, YAML — no Docker required."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ramalama",
    "claude-agent",
]

SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ramalama",
]


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
    # Pipe via stdin (as raw bytes, not text=True) to avoid Windows path issues
    # with Git Bash. Using text=True here would make Python's subprocess pipe
    # translate '\n' to '\r\n' on write, turning line-ending reserved words
    # like 'do'/'then' into the literal token 'do\r', which bash's parser
    # doesn't recognize — desyncing block nesting and surfacing as a bogus
    # "unexpected token" error further down the script.
    result = subprocess.run(
        ["bash", "-n", "-"],
        input=script.read_bytes(),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("feature", SU_DEV_FEATURES)
def test_pixi_calls_run_as_dev(feature):
    script = REPO_ROOT / "features" / feature / "install.sh"
    for line in script.read_text().splitlines():
        if "pixi global install" in line or "envs/dev/bin/pip" in line:
            assert "su dev -c" in line, f"{feature}: not run via su dev -c: {line!r}"


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


# --- claude-agent ---

@pytest.mark.parametrize("script", ["init-firewall.sh", "vibe"])
def test_claude_agent_script_syntax(script):
    path = REPO_ROOT / "features" / "claude-agent" / script
    # See test_install_sh_syntax above: bytes, not text=True, to dodge
    # Windows' \n -> \r\n pipe translation corrupting reserved-word tokens.
    result = subprocess.run(
        ["bash", "-n", "-"],
        input=path.read_bytes(),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_claude_agent_no_options():
    assert _feature_json("claude-agent").get("options", {}) == {}


# --- example devcontainer configs ---

def test_ramalama_sidecar_example_remote_user_dev():
    data = _devcontainer_json("examples/ramalama-sidecar/.devcontainer/devcontainer.json")
    assert data["remoteUser"] == "dev"


def test_claude_agent_consumer_declares_net_caps():
    # Any devcontainer.json under examples/ that references the claude-agent feature
    # must declare both NET_ADMIN and NET_RAW in runArgs, or init-firewall.sh fails at
    # container start (iptables/ipset need those caps). No example currently uses
    # claude-agent, so `referencing` is expected to be empty here — the loop below
    # then does nothing and the test passes, which is a legitimate pass (nothing to
    # violate the invariant), not a false negative from a skipped/uncollected test.
    referencing = [
        p for p in sorted((REPO_ROOT / "examples").glob("**/devcontainer.json"))
        if "ghcr.io/jesserobertson/devcontainers/claude-agent" in json.dumps(
            json.loads(p.read_text()).get("features", {})
        )
    ]
    for path in referencing:
        run_args = json.loads(path.read_text()).get("runArgs", [])
        rel = path.relative_to(REPO_ROOT)
        assert "--cap-add=NET_ADMIN" in run_args, f"{rel}: missing NET_ADMIN in runArgs"
        assert "--cap-add=NET_RAW" in run_args, f"{rel}: missing NET_RAW in runArgs"


def test_readme_no_root_remote_user():
    content = (REPO_ROOT / "README.md").read_text()
    assert '"remoteUser": "root"' not in content
    assert "/root/.cache/pixi" not in content


def test_readme_documents_claude_agent():
    assert "claude-agent" in (REPO_ROOT / "README.md").read_text()


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


# --- base Dockerfile ---

def _dockerfile_text() -> str:
    return (REPO_ROOT / "base" / "Dockerfile").read_text()


def test_dockerfile_creates_dev_user():
    assert "useradd -m -s /bin/bash dev" in _dockerfile_text()


def test_dockerfile_no_passwordless_sudo():
    assert "NOPASSWD" not in _dockerfile_text()


def test_dockerfile_ends_as_dev_user():
    lines = [l for l in _dockerfile_text().splitlines() if l.strip()]
    assert lines[-1].strip() == "USER dev"


def test_dockerfile_pixi_envs_owned_by_dev():
    assert "chown dev:dev /opt/pixi-envs" in _dockerfile_text()


# --- ramalama Dockerfile ---

def test_ramalama_dockerfile_explicit_root():
    content = (REPO_ROOT / "ramalama" / "Dockerfile").read_text()
    assert "USER root" in content


# --- helpers ---

def _feature_json(feature: str) -> dict:
    path = REPO_ROOT / "features" / feature / "devcontainer-feature.json"
    return json.loads(path.read_text())


def _devcontainer_json(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text())


def _yaml(rel_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / rel_path).read_text())
