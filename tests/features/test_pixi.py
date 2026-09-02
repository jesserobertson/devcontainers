"""Static checks for the pixi feature."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "pixi"


def _json() -> dict:
    return json.loads((FEATURE_DIR / "devcontainer-feature.json").read_text())


def test_id_and_options():
    data = _json()
    assert data["id"] == "pixi"
    opts = data["options"]
    assert opts["shellHook"]["default"] == "auto"
    assert set(opts["shellHook"]["enum"]) == {"auto", "off"}
    assert opts["global"]["default"] == ""


def test_container_env():
    env = _json()["containerEnv"]
    assert env["PIXI_HOME"] == "/home/dev/.local/share/pixi"
    assert env["PATH"] == "/home/dev/.local/share/pixi/bin:${PATH}"
    assert env["UV_HTTP_TIMEOUT"] == "300"


def test_installs_after_homebrew_and_shell_kit():
    after = _json()["installsAfter"]
    assert "ghcr.io/jesserobertson/devcontainers/homebrew" in after
    assert "ghcr.io/jesserobertson/devcontainers/shell-kit" in after


def test_no_depends_on():
    # pixi works standalone; ordering is installsAfter only.
    assert "dependsOn" not in _json()


def test_install_sh_guards_fish_snippet_and_runs_global_as_dev():
    script = (FEATURE_DIR / "install.sh").read_text()
    assert 'export PIXI_HOME="/home/dev/.local/share/pixi"' in script
    assert 'if [ "${SHELLHOOK:-auto}" = "auto" ]' in script
    assert "if [ -x /home/linuxbrew/.linuxbrew/bin/fish ]" in script
    assert 'if [ -n "${GLOBAL:-}" ]' in script
    assert "pixi global install" in script
    for line in script.splitlines():
        if "pixi global install" in line:
            assert "su dev -c" in line
