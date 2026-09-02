"""Static checks for the shell-kit feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "shell-kit"

BUNDLE = ["bat", "bat-extras", "eza", "fd", "fish", "fzf", "jq", "just",
         "neovim", "ripgrep", "starship", "zoxide"]


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_login_shell_option():
    data = _json()
    assert data["id"] == "shell-kit"
    assert data["options"]["loginShell"]["type"] == "boolean"
    assert data["options"]["loginShell"]["default"] is True


def test_depends_on_and_installs_after_homebrew():
    data = _json()
    assert data["dependsOn"] == {"ghcr.io/jesserobertson/devcontainers/homebrew": {}}
    assert "ghcr.io/jesserobertson/devcontainers/homebrew" in data["installsAfter"]


def test_container_env_shell_is_fish():
    assert _json()["containerEnv"]["SHELL"] == "/home/linuxbrew/.linuxbrew/bin/fish"


def test_install_sh_installs_full_bundle_as_dev_and_conditionally_chsh():
    script = (FEATURE_DIR / "install.sh").read_text()
    brew_line = next(l for l in script.splitlines() if "brew install" in l)
    assert "su dev -c" in brew_line or "su dev -c" in script.split("brew install")[0].splitlines()[-1]
    # Assert each package is its own whitespace-delimited token on the
    # `brew install` line - a bare substring check matches comments and lets
    # "bat" ride in on "bat-extras".
    installed = brew_line.split("brew install", 1)[1].strip().rstrip("'\"").split()
    for pkg in BUNDLE:
        assert pkg in installed, f"{pkg} missing from the brew install line"
    assert 'if [ "${LOGINSHELL:-true}" = "true" ]' in script
    assert "chsh -s /home/linuxbrew/.linuxbrew/bin/fish dev" in script
