"""Static checks for the homebrew feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "homebrew"


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_no_options():
    data = _json()
    assert data["id"] == "homebrew"
    assert data.get("options", {}) == {}


def test_container_env_puts_brew_on_path_and_silences_it():
    env = _json()["containerEnv"]
    assert env["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert env["HOMEBREW_NO_ANALYTICS"] == "1"
    assert "/home/linuxbrew/.linuxbrew/bin" in env["PATH"]
    assert env["PATH"].endswith(":${PATH}")


def test_install_sh_is_idempotent_and_installs_as_dev():
    script = (FEATURE_DIR / "install.sh").read_text()
    assert "if [ -x /home/linuxbrew/.linuxbrew/bin/brew ]" in script
    assert "chown dev:dev /home/linuxbrew" in script
    assert 'su dev -c ' in script
    installer_lines = [l for l in script.splitlines() if "install.sh" in l and "curl" in l]
    assert installer_lines and all("su dev -c" in l for l in installer_lines)
