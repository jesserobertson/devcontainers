"""Static validation: JSON structure, bash syntax, YAML — no Docker required."""

import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
    "agent", "podman", "rust-devtools", "cpp-devtools",
    "homebrew", "pixi", "shell-kit",
]

SU_DEV_FEATURES = [
    "rapids", "jax", "pytorch", "mojo", "marimo", "fastapi",
    "cli", "py-devtools", "huggingface", "transformers", "ollama",
]

GPU_TEMPLATE_FEATURES = ["rapids", "mojo", "jax", "pytorch", "transformers"]
CPU_TEMPLATE_FEATURES = [
    "marimo", "fastapi", "cli", "py-devtools", "huggingface", "ollama", "podman",
]

SLIM_TEMPLATE_FEATURES = ["rust-devtools", "cpp-devtools"]

# Plumbing features extracted from base/Dockerfile - published, composed onto
# base images, and depended on by other features. They have no template dir.
PLUMBING_FEATURES = ["homebrew", "pixi", "shell-kit"]

# Templates whose postCreateCommand must set pixi detached-environments -
# everything except the slim-based ones, which run no pixi at all.
PIXI_TEMPLATE_FEATURES = [f for f in FEATURES if f not in SLIM_TEMPLATE_FEATURES + PLUMBING_FEATURES]


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


BREW_FEATURES = ["rust-devtools", "cpp-devtools"]


@pytest.mark.parametrize("feature", BREW_FEATURES)
def test_brew_calls_run_as_dev(feature):
    script = (REPO_ROOT / "features" / feature / "install.sh").read_text()
    brew_lines = [l for l in script.splitlines() if "brew install" in l]
    assert brew_lines, f"{feature}: no 'brew install' line in install.sh"
    for line in brew_lines:
        assert "su dev -c" in line, f"{feature}: brew install not via su dev -c: {line!r}"


@pytest.mark.parametrize("feature", BREW_FEATURES)
def test_brew_feature_depends_on_homebrew(feature):
    data = _feature_json(feature)
    assert data["dependsOn"] == {"ghcr.io/jesserobertson/devcontainers/homebrew": {}}


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


# --- ollama ---

def test_ollama_has_five_options():
    assert set(_feature_json("ollama")["options"]) == {"host", "port", "model", "apiKey", "contextSize"}


@pytest.mark.parametrize("option,expected", [
    ("host",        "host.docker.internal"),
    ("port",        "11434"),
    ("model",       "llama3.2"),
    ("apiKey",      "ollama"),
    ("contextSize", "4096"),
])
def test_ollama_option_default(option, expected):
    assert _feature_json("ollama")["options"][option]["default"] == expected


def test_ollama_no_container_env():
    assert "containerEnv" not in _feature_json("ollama")


# --- agent ---

@pytest.mark.parametrize("script", ["init-firewall.sh", "vibe"])
def test_agent_script_syntax(script):
    path = REPO_ROOT / "features" / "agent" / script
    # See test_install_sh_syntax above: bytes, not text=True, to dodge
    # Windows' \n -> \r\n pipe translation corrupting reserved-word tokens.
    result = subprocess.run(
        ["bash", "-n", "-"],
        input=path.read_bytes(),
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_agent_no_options():
    assert _feature_json("agent").get("options", {}) == {}


# --- example devcontainer configs ---

def test_ollama_sidecar_example_remote_user_dev():
    data = _devcontainer_json("examples/ollama-sidecar/.devcontainer/devcontainer.json")
    assert data["remoteUser"] == "dev"


def test_no_pgrep_sshd_anywhere():
    # Confirmed dead code: DevPod's in-container SSH server is an embedded Go
    # binary (cmd/agent/container/ssh_server.go), never a process named sshd.
    # See docs/superpowers/specs/2026-07-23-cli-first-templates-design.md.
    offenders = []
    if "pgrep sshd" in (REPO_ROOT / "README.md").read_text():
        offenders.append("README.md")
    for path in sorted(REPO_ROOT.glob("examples/**/devcontainer.json")):
        if "pgrep sshd" in path.read_text():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    for path in sorted(REPO_ROOT.glob("templates/**/devcontainer.json")):
        if "pgrep sshd" in path.read_text():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"pgrep sshd wait-loop found in: {offenders}"


def test_agent_consumer_declares_net_caps():
    # Any devcontainer.json under examples/ that references the agent feature must
    # declare both NET_ADMIN and NET_RAW in runArgs, or init-firewall.sh fails at
    # container start (iptables/ipset need those caps). No example currently uses
    # agent, so `referencing` is expected to be empty here — the loop below then
    # does nothing and the test passes, which is a legitimate pass (nothing to
    # violate the invariant), not a false negative from a skipped/uncollected test.
    referencing = [
        p for p in sorted((REPO_ROOT / "examples").glob("**/devcontainer.json"))
        if "ghcr.io/jesserobertson/devcontainers/agent" in json.dumps(
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


def test_readme_documents_agent():
    assert "agent" in (REPO_ROOT / "README.md").read_text()


def test_readme_documents_base_ubuntu_slim():
    assert "base-ubuntu-slim" in (REPO_ROOT / "README.md").read_text()


# --- templates/ (standalone per-feature devcontainer.json) ---

@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_uses_base_cuda(feature):
    assert _template_json(feature)["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_requests_gpus(feature):
    assert _template_json(feature)["runArgs"] == ["--gpus", "all"]


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", GPU_TEMPLATE_FEATURES)
def test_gpu_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_uses_base_ubuntu(feature):
    assert _template_json(feature)["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", CPU_TEMPLATE_FEATURES)
def test_cpu_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_uses_base_ubuntu_slim(feature):
    assert (
        _template_json(feature)["image"]
        == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"
    )


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_references_own_feature(feature):
    data = _template_json(feature)
    assert f"ghcr.io/jesserobertson/devcontainers/{feature}:latest" in data["features"]


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_remote_user_dev(feature):
    assert _template_json(feature)["remoteUser"] == "dev"


@pytest.mark.parametrize("feature", SLIM_TEMPLATE_FEATURES)
def test_slim_template_no_sshd_waitloop(feature):
    assert "pgrep sshd" not in json.dumps(_template_json(feature))


def test_agent_template_uses_base_ubuntu():
    assert _template_json("agent")["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_agent_template_references_own_feature():
    data = _template_json("agent")
    assert "ghcr.io/jesserobertson/devcontainers/agent:latest" in data["features"]


def test_agent_template_remote_user_dev():
    assert _template_json("agent")["remoteUser"] == "dev"


def test_agent_template_declares_firewall_caps():
    run_args = _template_json("agent")["runArgs"]
    assert "--cap-add=NET_ADMIN" in run_args
    assert "--cap-add=NET_RAW" in run_args


def test_agent_template_arms_firewall_on_start():
    data = _template_json("agent")
    assert data["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert data["waitFor"] == "postStartCommand"


def test_agent_template_no_sshd_waitloop():
    assert "pgrep sshd" not in json.dumps(_template_json("agent"))


@pytest.mark.parametrize("feature", PIXI_TEMPLATE_FEATURES)
def test_template_post_create_enables_detached_environments(feature):
    # Every template's postCreateCommand is a plain string, and dvt's merge
    # algorithm replaces (rather than combines) plain-string lifecycle
    # commands outright - so `dvt feature add` silently drops whatever
    # postCreateCommand came before it, including `dvt init`'s own
    # detached-environments setup step (see
    # devtemplate.commands.init.POST_CREATE_COMMAND). Without this step in
    # every template too, `pixi install` writes .pixi/envs straight onto
    # the bind-mounted workspace, which fails outright on Windows hosts
    # ("Operation not permitted" copying into the mount - confirmed live
    # against a real container, 2026-08-15).
    assert "detached-environments = true" in _template_json(feature)["postCreateCommand"]


# --- compose YAML ---

@pytest.mark.parametrize("rel_path", [
    "examples/ollama-sidecar/.devcontainer/docker-compose.yml",
    "host-services/ollama/docker-compose.yml",
])
def test_compose_valid_yaml(rel_path):
    data = _yaml(rel_path)
    assert "services" in data


def test_example_sidecar_has_app_and_ollama():
    data = _yaml("examples/ollama-sidecar/.devcontainer/docker-compose.yml")
    assert "app" in data["services"]
    assert "ollama" in data["services"]


def test_example_sidecar_ollama_image():
    data = _yaml("examples/ollama-sidecar/.devcontainer/docker-compose.yml")
    assert data["services"]["ollama"]["image"] == "ollama/ollama:latest"


def test_example_sidecar_gpu_config():
    data = _yaml("examples/ollama-sidecar/.devcontainer/docker-compose.yml")
    devices = data["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert any(d.get("driver") == "nvidia" for d in devices)


def test_host_services_model_volume():
    data = _yaml("host-services/ollama/docker-compose.yml")
    volumes = data["services"]["ollama"].get("volumes", [])
    assert any("/root/.ollama" in str(v) for v in volumes)


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


def test_dockerfile_does_not_set_pixi_home():
    # PIXI_HOME now lives only in the pixi feature's containerEnv.
    assert "PIXI_HOME" not in _dockerfile_text()


def _dockerfile_stage(name: str) -> str:
    """Return the text of one multi-stage build stage: the `FROM … AS <name>`
    line through to (not including) the next top-level `FROM` line."""
    lines = _dockerfile_text().splitlines()
    start = next(
        (i for i, l in enumerate(lines)
         if re.match(rf"^FROM\s+\S+\s+AS\s+{re.escape(name)}\s*$", l)),
        None,
    )
    assert start is not None, f"no Dockerfile stage named {name!r}"
    end = next(
        (j for j in range(start + 1, len(lines)) if lines[j].startswith("FROM ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_dockerfile_has_core_and_slim_stages_only():
    text = _dockerfile_text()
    assert re.search(r"^FROM \S+ AS core$", text, re.M)
    assert re.search(r"^FROM core AS slim$", text, re.M)
    assert not re.search(r"^FROM \S+ AS full$", text, re.M), "the full stage must be gone"


def test_dockerfile_core_stage_has_no_pixi_no_brew_no_cli_bundle():
    core = _dockerfile_stage("core")
    assert "pixi.sh/install.sh" not in core
    assert "PIXI_HOME" not in core
    assert "brew install" not in core
    assert "Homebrew/install/HEAD/install.sh" not in core


def test_dockerfile_core_stage_has_no_cli_bundle():
    assert "brew install" not in _dockerfile_stage("core")


def test_dockerfile_slim_stage_adds_nothing():
    assert "RUN" not in _dockerfile_stage("slim")


# --- published Feature versions vs local content ---
#
# 2026-08-15: every feature's install.sh had drifted from what's actually
# published on GHCR for months, because devcontainer-feature.json's
# "version" field was never bumped alongside it. devcontainers/action's
# publish step is version-keyed - pushing an unchanged version against an
# already-published artifact is a silent no-op, so every CI run reported
# success while publishing nothing new. Confirmed directly: pulling
# ghcr.io/jesserobertson/devcontainers/py-devtools:latest fresh returned a
# script that predated a refactor from over a month earlier. This section
# guards against the same drift recurring: for any feature whose current
# local version *is already published*, local content must match exactly -
# if it doesn't, the fix is to bump the version, not to touch this test.

GHCR_REGISTRY = "ghcr.io"
GHCR_REPOSITORY_PREFIX = "jesserobertson/devcontainers"
MANIFEST_ACCEPT = "application/vnd.oci.image.manifest.v1+json"


def _published_feature_files(feature: str, version: str) -> dict[str, str] | None:
    """Fetch devcontainer-feature.json + install.sh as published under
    ghcr.io/jesserobertson/devcontainers/<feature>:<version>, or None if
    that exact version hasn't been published (a 404 manifest lookup) - the
    expected, passing state right after bumping a version locally, before
    it's been published by CI yet.

    Raises (via pytest.skip, from the caller) rather than failing outright
    on any other network trouble - GHCR being briefly unreachable from CI
    is an infra concern, not evidence of a real version-drift bug.
    """
    import httpx

    repository = f"{GHCR_REPOSITORY_PREFIX}/{feature}"
    manifest_url = f"https://{GHCR_REGISTRY}/v2/{repository}/manifests/{version}"

    with httpx.Client(timeout=15.0) as client:
        probe = client.get(manifest_url, headers={"Accept": MANIFEST_ACCEPT})
        if probe.status_code == 401:
            challenge = probe.headers.get("www-authenticate")
            if challenge is None:
                raise RuntimeError(f"401 from {GHCR_REGISTRY} had no WWW-Authenticate header")
            params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            token = client.get(
                params["realm"],
                params={"service": params["service"], "scope": params["scope"]},
            ).json()["token"]
            probe = client.get(
                manifest_url,
                headers={"Accept": MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
            )
        else:
            token = None

        if probe.status_code == 404:
            return None
        probe.raise_for_status()
        manifest = probe.json()
        digest = manifest["layers"][0]["digest"]

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        blob = client.get(
            f"https://{GHCR_REGISTRY}/v2/{repository}/blobs/{digest}",
            headers=headers,
            follow_redirects=True,
        )
        blob.raise_for_status()

    with tarfile.open(fileobj=io.BytesIO(blob.content), mode="r:") as tar:
        # Member names come back "./install.sh", not "install.sh" - lstrip
        # the tar's own leading "./" convention, not arbitrary path chars.
        members = {
            m.name[2:] if m.name.startswith("./") else m.name: m
            for m in tar.getmembers()
        }
        result = {}
        for name in ("devcontainer-feature.json", "install.sh"):
            member = members.get(name)
            if member is None:
                continue
            extracted = tar.extractfile(member)
            if extracted is not None:
                result[name] = extracted.read().decode()
        return result


@pytest.mark.parametrize("feature", FEATURES)
def test_published_feature_version_matches_local_content(feature):
    local_version = _feature_json(feature)["version"]
    local_install_sh = (REPO_ROOT / "features" / feature / "install.sh").read_text()

    try:
        published = _published_feature_files(feature, local_version)
    except Exception as exc:  # noqa: BLE001 - network trouble, not a drift finding
        pytest.skip(f"could not reach GHCR to verify {feature}:{local_version}: {exc}")
        return

    if published is None:
        # Not published yet under this version - the expected state right
        # after a local version bump, before CI has published it.
        return

    assert published.get("install.sh") == local_install_sh, (
        f"features/{feature}/install.sh has changed since {feature}:{local_version} "
        f"was published on GHCR, but the version wasn't bumped - bump "
        f"devcontainer-feature.json's \"version\" so this actually gets republished "
        f"(see 2026-08-15's incident above)."
    )


# --- images/ registry ---

IMAGES = ["base-ubuntu", "base-cuda", "base-ubuntu-slim", "base-cuda-slim"]


@pytest.mark.parametrize("image", IMAGES)
def test_image_json_has_required_fields(image):
    data = _image_json(image)
    for field in ("name", "description", "ref", "aliases"):
        assert field in data, f"missing field '{field}' in {image}"


@pytest.mark.parametrize("image", IMAGES)
def test_image_json_name_matches_filename(image):
    assert _image_json(image)["name"] == image


def test_base_ubuntu_ref_matches_cpu_templates():
    assert (
        _image_json("base-ubuntu")["ref"] == "ghcr.io/jesserobertson/base-ubuntu:latest"
    )


def test_base_cuda_ref_matches_gpu_templates():
    assert _image_json("base-cuda")["ref"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_base_ubuntu_slim_ref():
    assert (
        _image_json("base-ubuntu-slim")["ref"]
        == "ghcr.io/jesserobertson/base-ubuntu-slim:latest"
    )


def test_base_cuda_slim_ref():
    assert (
        _image_json("base-cuda-slim")["ref"]
        == "ghcr.io/jesserobertson/base-cuda-slim:latest"
    )


BUNDLE_CONFIGS = {
    "base-ubuntu": "ghcr.io/jesserobertson/base-ubuntu-slim:latest",
    "base-cuda": "ghcr.io/jesserobertson/base-cuda-slim:latest",
}
PLUMBING_FEATURE_REFS = {
    "ghcr.io/jesserobertson/devcontainers/homebrew:latest",
    "ghcr.io/jesserobertson/devcontainers/shell-kit:latest",
    "ghcr.io/jesserobertson/devcontainers/pixi:latest",
}


@pytest.mark.parametrize("bundle,base_ref", BUNDLE_CONFIGS.items())
def test_bundle_config_composes_slim_plus_three_features(bundle, base_ref):
    cfg = _devcontainer_json(f"images/{bundle}/.devcontainer/devcontainer.json")
    assert cfg["image"] == base_ref
    assert set(cfg["features"]) == PLUMBING_FEATURE_REFS


# --- build workflow ---

def test_build_yml_has_slim_and_bundle_jobs():
    data = _yaml(".github/workflows/build.yml")
    jobs = data["jobs"]
    assert set(jobs) == {"build-slim", "build-bundles"}
    assert jobs["build-bundles"]["needs"] == "build-slim"
    slim_names = {m["name"] for m in jobs["build-slim"]["strategy"]["matrix"]["include"]}
    assert slim_names == {"base-ubuntu-slim", "base-cuda-slim"}
    bundle_names = {m["name"] for m in jobs["build-bundles"]["strategy"]["matrix"]["include"]}
    assert bundle_names == {"base-ubuntu", "base-cuda"}


# --- helpers ---

def _feature_json(feature: str) -> dict:
    path = REPO_ROOT / "features" / feature / "devcontainer-feature.json"
    return json.loads(path.read_text())


def _devcontainer_json(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text())


def _yaml(rel_path: str) -> dict:
    return yaml.safe_load((REPO_ROOT / rel_path).read_text())


def _template_json(feature: str) -> dict:
    path = REPO_ROOT / "templates" / feature / "devcontainer.json"
    return json.loads(path.read_text())


def _image_json(image: str) -> dict:
    path = REPO_ROOT / "images" / f"{image}.json"
    return json.loads(path.read_text())
