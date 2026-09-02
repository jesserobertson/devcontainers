"""Assemble the bundle images from features and probe the result.

Requires Docker and @devcontainers/cli. Every test here is skipped when the
Docker daemon is unreachable; the three that shell out to ``devcontainer
build`` are skipped again when the ``devcontainers`` CLI is absent.

The ``devcontainer build`` tests build from configs that reference
``ghcr.io/jesserobertson/devcontainers/{homebrew,shell-kit,pixi,rust-devtools}:latest``.
Those refs resolve only once the features are published (CI, post-merge) or
when the run supplies a local feature override. Until then a local invocation
that gets past the skip guards will fail at feature pull, not pass silently --
that is expected and is why CI is the real gate for this module.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

# Image tags this module builds. The cleanup fixture removes every one of them
# after each test so a failed run never leaves dangling images behind.
_TEST_IMAGES = (
    "base-ubuntu-itest",
    "slim-itest",
    "pixi-on-slim-itest",
    "rust-on-slim-itest",
)


def _docker_ok() -> bool:
    """True when a docker client is on PATH and its daemon answers."""
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except OSError:
        return False


def _devcontainer_ok() -> bool:
    """True when the @devcontainers/cli 'devcontainer' binary is on PATH."""
    return shutil.which("devcontainer") is not None


# tests/conftest.py only wires up the --gpu opt-in; there is no shared
# docker-availability gate, so this module carries its own.
pytestmark = pytest.mark.skipif(
    not _docker_ok(), reason="Docker daemon not available"
)

_needs_devcontainer_cli = pytest.mark.skipif(
    not _devcontainer_ok(), reason="@devcontainers/cli not installed"
)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    """subprocess.run with the capture/text defaults every probe here wants."""
    return subprocess.run(cmd, capture_output=True, text=True)


def _write_devcontainer(tmp_path: Path, config: dict) -> Path:
    """Drop a throwaway .devcontainer/devcontainer.json under tmp_path.

    Returns the workspace folder to hand to ``devcontainer build``.
    """
    dc_dir = tmp_path / ".devcontainer"
    dc_dir.mkdir(parents=True, exist_ok=True)
    (dc_dir / "devcontainer.json").write_text(json.dumps(config, indent=2))
    return tmp_path


@pytest.fixture
def cleanup_images():
    """Force-remove every image tag this module might create, post-test."""
    yield
    for name in _TEST_IMAGES:
        subprocess.run(["docker", "rmi", "-f", name], capture_output=True)


@_needs_devcontainer_cli
def test_base_ubuntu_assembly_has_fish_brew_pixi(cleanup_images):
    """The assembled base-ubuntu bundle carries fish, brew and pixi, and the
    bash pixi shell-hook activates for a /workspace manifest."""
    build = _run([
        "devcontainer", "build",
        "--workspace-folder", str(REPO_ROOT / "images" / "base-ubuntu"),
        "--image-name", "base-ubuntu-itest",
    ])
    assert build.returncode == 0, build.stderr

    tools = _run([
        "docker", "run", "--rm", "--user", "dev", "base-ubuntu-itest",
        "bash", "-lc", "command -v fish && command -v brew && command -v pixi",
    ])
    assert tools.returncode == 0, f"stdout={tools.stdout}\nstderr={tools.stderr}"

    # Drop a valid pixi workspace manifest in /workspace, then source the
    # feature-written .bashrc snippet: it should eval `pixi shell-hook` and
    # export PIXI_PROJECT_ROOT=/workspace.
    manifest = (
        "[project]\\n"
        'name = "probe"\\n'
        'version = "0.0.0"\\n'
        "\\n"
        "[tool.pixi.workspace]\\n"
        'channels = ["conda-forge"]\\n'
        'platforms = ["linux-64"]\\n'
    )
    hook = _run([
        "docker", "run", "--rm", "--user", "dev", "-w", "/workspace",
        "base-ubuntu-itest", "bash", "-c",
        f"printf '{manifest}' > /workspace/pyproject.toml && "
        "source /home/dev/.bashrc && "
        'echo "HOOK_ROOT=${PIXI_PROJECT_ROOT:-none}"',
    ])
    assert "HOOK_ROOT=/workspace" in hook.stdout, (
        f"stdout={hook.stdout}\nstderr={hook.stderr}"
    )


def test_base_ubuntu_slim_has_none_of_them(cleanup_images):
    """The published slim base bundles none of fish / brew / pixi."""
    build = _run([
        "docker", "build", "--target", "slim", "-t", "slim-itest",
        str(REPO_ROOT / "base"),
    ])
    assert build.returncode == 0, build.stderr

    for tool in ("fish", "brew", "pixi"):
        probe = _run([
            "docker", "run", "--rm", "--user", "dev", "slim-itest",
            "bash", "-lc", f"command -v {tool}",
        ])
        assert probe.returncode != 0, (
            f"{tool} unexpectedly present on slim: {probe.stdout}"
        )


@_needs_devcontainer_cli
def test_pixi_feature_writes_bash_hook_not_fish_on_slim(tmp_path, cleanup_images):
    """On slim (no fish) the pixi feature writes the bash hook only -- the fish
    conf.d snippet is skipped because fish is absent."""
    slim = _run([
        "docker", "build", "--target", "slim", "-t", "slim-itest",
        str(REPO_ROOT / "base"),
    ])
    assert slim.returncode == 0, slim.stderr

    workspace = _write_devcontainer(tmp_path, {
        "name": "pixi-on-slim-itest",
        "image": "slim-itest",
        "features": {
            "ghcr.io/jesserobertson/devcontainers/pixi:latest": {},
        },
    })
    build = _run([
        "devcontainer", "build",
        "--workspace-folder", str(workspace),
        "--image-name", "pixi-on-slim-itest",
    ])
    assert build.returncode == 0, build.stderr

    bashrc = _run([
        "docker", "run", "--rm", "--user", "dev", "pixi-on-slim-itest",
        "cat", "/home/dev/.bashrc",
    ])
    assert bashrc.returncode == 0, bashrc.stderr
    assert "pixi shell-hook" in bashrc.stdout, bashrc.stdout

    fish_snippet = _run([
        "docker", "run", "--rm", "--user", "dev", "pixi-on-slim-itest",
        "bash", "-lc", "test -e /home/dev/.config/fish/conf.d/project-pixi.fish",
    ])
    assert fish_snippet.returncode != 0, (
        "/home/dev/.config/fish/conf.d/project-pixi.fish should not exist on slim"
    )


@_needs_devcontainer_cli
def test_rust_devtools_on_slim_pulls_homebrew(tmp_path, cleanup_images):
    """rust-devtools on slim drags Homebrew in via `dependsOn: homebrew`, so
    both brew and cargo end up on PATH."""
    workspace = _write_devcontainer(tmp_path, {
        "name": "rust-on-slim-itest",
        "image": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
        "features": {
            "ghcr.io/jesserobertson/devcontainers/rust-devtools:latest": {},
        },
    })
    build = _run([
        "devcontainer", "build",
        "--workspace-folder", str(workspace),
        "--image-name", "rust-on-slim-itest",
    ])
    assert build.returncode == 0, build.stderr

    probe = _run([
        "docker", "run", "--rm", "--user", "dev", "rust-on-slim-itest",
        "bash", "-lc", "command -v brew && command -v cargo",
    ])
    assert probe.returncode == 0, f"stdout={probe.stdout}\nstderr={probe.stderr}"
