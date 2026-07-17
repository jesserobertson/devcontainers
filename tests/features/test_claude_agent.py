"""Docker tests for the claude-agent feature: install.sh side effects and a live firewall exercise."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
FEATURE_DIR = REPO_ROOT / "features" / "claude-agent"

CURL_STUB = """#!/bin/bash
if [[ "$*" == *claude.ai/install.sh* ]]; then
    echo 'mkdir -p ~/.local/bin && touch ~/.local/bin/claude && chmod +x ~/.local/bin/claude'
elif [[ "$*" == *pi.dev/install.sh* ]]; then
    echo 'mkdir -p ~/.local/bin && touch ~/.local/bin/pi && chmod +x ~/.local/bin/pi'
elif [[ "$*" == *omp.sh/install* ]]; then
    echo 'mkdir -p ~/.local/bin && touch ~/.local/bin/omp && chmod +x ~/.local/bin/omp'
fi
"""


# ---------------------------------------------------------------------------
# install.sh side effects (stubbed curl, no real network)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def installed_container(tmp_path_factory):
    cid = subprocess.check_output(
        ["docker", "run", "-d", "ubuntu:24.04", "sleep", "infinity"], text=True
    ).strip()
    try:
        _prepare(cid, tmp_path_factory.mktemp("claude-agent-stub"))
        subprocess.run(
            ["docker", "exec", cid, "bash", "/tmp/claude-agent/install.sh"],
            check=True,
        )
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_init_firewall_installed_root_only(installed_container):
    assert _stat(installed_container, "/usr/local/bin/init-firewall.sh") == "700 root root"


def test_vibe_installed_executable_by_dev(installed_container):
    assert _stat(installed_container, "/home/dev/.local/bin/vibe") == "755 dev dev"


def test_vibe_defaults_to_omp_yolo(installed_container):
    content = _exec(installed_container, "cat /home/dev/.local/bin/vibe")
    assert "exec omp --yolo" in content


def test_vibe_claude_execs_claude_with_skip_permissions(installed_container):
    content = _exec(installed_container, "cat /home/dev/.local/bin/vibe")
    assert '"${1:-}" = "claude"' in content
    assert "--dangerously-skip-permissions" in content


def test_sudoers_rule_scoped_to_firewall_script(installed_container):
    content = _exec(installed_container, "cat /etc/sudoers.d/claude-agent")
    assert content.strip() == "dev ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh"


def test_sudoers_file_permissions(installed_container):
    assert _stat(installed_container, "/etc/sudoers.d/claude-agent") == "440 root root"


def test_claude_installed_for_dev_user(installed_container):
    result = subprocess.run(
        ["docker", "exec", installed_container, "test", "-f", "/home/dev/.local/bin/claude"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_pi_config_dir_precreated_dev_owned(installed_container):
    # Consumers that volume-mount ~/.pi (to persist /login state, e.g. kidinnu's
    # devcontainer.json) need this path to exist and be dev-owned *before* the
    # mount happens - otherwise Docker creates the mount point as root:root and
    # dev can never write there (no sudo for chown). Confirmed directly: without
    # pre-creating this in install.sh, `touch ~/.pi/x` as dev fails with
    # "Permission denied" once a volume is mounted at that path.
    assert _stat(installed_container, "/home/dev/.pi") == "755 dev dev"


def test_omp_config_dir_precreated_dev_owned(installed_container):
    assert _stat(installed_container, "/home/dev/.omp") == "755 dev dev"


def test_pi_installed_for_dev_user(installed_container):
    result = subprocess.run(
        ["docker", "exec", installed_container, "test", "-f", "/home/dev/.local/bin/pi"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_omp_installed_for_dev_user(installed_container):
    result = subprocess.run(
        ["docker", "exec", installed_container, "test", "-f", "/home/dev/.local/bin/omp"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_npm_prefix_set_before_pi_install(installed_container):
    # install.sh must fully-qualify this npm call (/home/dev/.local/share/pixi/bin/npm) —
    # `su dev -c` doesn't inherit PATH, so a bare `npm` would silently no-op under
    # `set -e` only if npm itself were missing; here the mock proves the call actually
    # ran by writing its args, catching a regression back to the bare-`npm` bug.
    content = _exec(installed_container, "cat /home/dev/.local/share/pixi/bin/npm.log")
    assert "config set prefix" in content


# ---------------------------------------------------------------------------
# Live firewall exercise: does it actually block/allow the right hosts?
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def firewalled_container():
    cid = subprocess.check_output(
        ["docker", "run", "-d", "--cap-add=NET_ADMIN", "--cap-add=NET_RAW",
         "ubuntu:24.04", "sleep", "infinity"],
        text=True,
    ).strip()
    try:
        subprocess.run(
            ["docker", "exec", cid, "bash", "-c",
             "apt-get update && apt-get install -y iptables ipset dnsutils jq aggregate iproute2 curl"],
            check=True,
        )
        subprocess.run(
            ["docker", "cp", str(FEATURE_DIR / "init-firewall.sh"),
             f"{cid}:/usr/local/bin/init-firewall.sh"],
            check=True,
        )
        subprocess.run(["docker", "exec", cid, "chmod", "+x", "/usr/local/bin/init-firewall.sh"], check=True)
        subprocess.run(["docker", "exec", cid, "/usr/local/bin/init-firewall.sh"], check=True)
        yield cid
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_firewall_blocks_non_allowlisted_host(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://example.com"],
        capture_output=True,
    )
    assert result.returncode != 0


def test_firewall_allows_github(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://api.github.com/zen"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_firewall_armed_marker_written(firewalled_container):
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "test", "-f", "/run/firewall-armed"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_firewall_survives_a_second_run(firewalled_container):
    """A container restart re-runs postStartCommand, so init-firewall.sh must be
    safely re-runnable. `iptables -F` clears rules but not a chain's default
    policy — without resetting policies to ACCEPT first, a second run inherits
    the first run's OUTPUT DROP policy and hangs on its own DNS/curl setup
    calls before the new ALLOW rules are back in place."""
    result = subprocess.run(
        ["docker", "exec", firewalled_container, "/usr/local/bin/init-firewall.sh"],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    blocked = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://example.com"],
        capture_output=True,
    )
    assert blocked.returncode != 0

    allowed = subprocess.run(
        ["docker", "exec", firewalled_container, "curl", "--connect-timeout", "5", "https://api.github.com/zen"],
        capture_output=True,
    )
    assert allowed.returncode == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prepare(cid: str, tmp_path: Path) -> None:
    """Create the dev user, stub curl for the claude.ai/pi.dev/omp.sh installers, stub
    pixi and npm at the fully-qualified paths install.sh actually invokes (bare ubuntu:24.04
    has neither — real base/Dockerfile installs pixi under PIXI_HOME, nodejs's npm shim
    lands alongside it), and copy the feature in."""
    subprocess.run(["docker", "exec", cid, "useradd", "-m", "-s", "/bin/bash", "dev"], check=True)

    stub = tmp_path / "curl"
    # newline="\n" forces LF-only output: on Windows, write_text()'s default
    # universal-newline translation would turn the shebang line into
    # "#!/bin/bash\r\n", and Linux refuses to exec a script whose shebang
    # ends in \r ("cannot execute: required file not found").
    stub.write_text(CURL_STUB, newline="\n")
    stub.chmod(0o755)
    subprocess.run(["docker", "cp", str(stub), f"{cid}:/usr/local/bin/curl"], check=True)

    subprocess.run(
        ["docker", "exec", cid, "bash", "-c",
         "mkdir -p /home/dev/.local/share/pixi/bin && "
         "printf '#!/bin/bash\\necho pixi: $@\\n' > /home/dev/.local/share/pixi/bin/pixi && "
         "printf '#!/bin/bash\\necho npm: \"$@\" >> /home/dev/.local/share/pixi/bin/npm.log\\n'"
         " > /home/dev/.local/share/pixi/bin/npm && "
         "chmod +x /home/dev/.local/share/pixi/bin/pixi /home/dev/.local/share/pixi/bin/npm && "
         "chown -R dev:dev /home/dev/.local"],
        check=True,
    )

    subprocess.run(["docker", "exec", cid, "mkdir", "-p", "/tmp/claude-agent"], check=True)
    for name in ("install.sh", "init-firewall.sh", "vibe", "devcontainer-feature.json"):
        subprocess.run(
            ["docker", "cp", str(FEATURE_DIR / name), f"{cid}:/tmp/claude-agent/{name}"],
            check=True,
        )
    subprocess.run(["docker", "exec", cid, "chmod", "+x", "/tmp/claude-agent/install.sh"], check=True)


def _exec(cid: str, cmd: str) -> str:
    return subprocess.check_output(["docker", "exec", cid, "bash", "-c", cmd], text=True).strip()


def _stat(cid: str, path: str) -> str:
    return _exec(cid, f"stat -c '%a %U %G' {path}")
