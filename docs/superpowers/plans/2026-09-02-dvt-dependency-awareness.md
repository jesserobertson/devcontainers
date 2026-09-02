# dvt Dependency Awareness — Implementation Plan

> **SUPERSEDED 2026-09-03.** Written before `feature_graph.py` shipped (commit `6ed7268`).
> Its GitHub-raw spec cache (`feature_specs.py`, new `github.py` fetchers) and standalone
> `deps.py` resolver are no longer needed. See
> [`docs/superpowers/specs/2026-09-03-dvt-dependency-awareness-v2.md`](../specs/2026-09-03-dvt-dependency-awareness-v2.md)
> and its implementation plan. Do not execute this document.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `dvt` the feature dependency graph — `dvt sync` fetches every `devcontainer-feature.json`, a pure resolver walks the `dependsOn` edges, and four UI surfaces expose it: a "Pulls in" column in `dvt feature list`, a dependency tree in `dvt feature show`, a new `dvt feature deps` command (`tree`/`dot`/`mermaid`/`json`), and an "also pulling in" message at `dvt feature add` time.

**Architecture:** A new per-feature spec cache (`<data_dir>/feature-specs/<id>.json`, its own manifest) sits beside the existing template and image caches, populated from `raw.githubusercontent.com/<repo>/<branch>/features/<id>/devcontainer-feature.json` during `dvt sync`. A pure `devtemplate/deps.py` module loads that cache into `FeatureRecord`s and computes a transitive `dependsOn` closure (`Resolution`), with `installsAfter` carried as annotation only. The `feature` command module consumes `deps.resolve` for all four surfaces. No change to `dvt up` / feature application — `dependsOn` is applied by the devcontainer spec at build time; `dvt` only *reports* it.

**Tech Stack:** Python 3.12+, Typer (`devtemplate.describe.Typer`), Rich (`Table`, `Tree`), httpx, `logerr` (`Result`/`Ok`/`Err`, `wrap_result`, `@on_err` retry), pydantic-settings, pytest + `pytest.mark` conventions already in `dvt/tests/`.

**Spec:** `docs/superpowers/specs/2026-09-02-shell-cli-features-design.md` (§"`dvt` — dependency awareness")

## Global Constraints

- All work is under `dvt/` (run every command from `dvt/`). Package import root is `devtemplate`.
- Feature spec source URL: `https://raw.githubusercontent.com/{settings.github_repo}/{settings.github_branch}/features/{id}/devcontainer-feature.json`. Directory listing: `https://api.github.com/repos/{settings.github_repo}/contents/features?ref={settings.github_branch}`.
- Feature ref prefix is `ghcr.io/jesserobertson/devcontainers/<id>`. `ref_to_id` strips any `:tag`/`@digest` then takes the last path segment. A `dependsOn` ref outside the local cache (e.g. `ghcr.io/devcontainers/features/common-utils`) is kept verbatim in `pulls_in` and not recursed into — surfaced, never fatal.
- `dependsOn` in a `devcontainer-feature.json` is an **object** (`{ "<ref>": {...} }`); normalise to a sorted list of ref keys. `installsAfter` is already a list.
- The existing `settings.features_dir` (`<data_dir>/features`) is the OCI-artifact pull cache that `dvt sync` wipes via `clear_pulled_features` — **do not reuse it**. The new cache is `settings.feature_specs_dir`.
- Every new public function returns `Result[..., Exception]` where the codebase's siblings do (`sync_templates`, `load_cached_template` are `@wrap_result`); `resolve` returns `Result[Resolution, Exception]` (Err on dependency cycle).
- Follow existing module conventions: `from __future__ import annotations`, explicit `__all__`, `@wrap_result` for fallible IO helpers, `@on_err(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=2), log_attempts=True)` on network fetchers.
- Missing/empty `feature_specs_dir` must never hard-fail `dvt feature list` or `dvt feature add` — degrade to `—` / a hint to run `dvt sync`.
- `dvt` version (`dvt/pyproject.toml`) currently `0.4.1`; bump to `0.5.0` in the final task. Update `dvt/CHANGELOG.md`.
- Commit after every green step. Stage explicit paths, never `git add -A`.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku
  ```

---

## File Structure

**Created:**
- `dvt/src/devtemplate/feature_specs.py` — the spec cache: `sync_feature_specs`, `list_cached_feature_specs`, `load_cached_feature_spec`, manifest read/write. Mirrors `images.py`.
- `dvt/src/devtemplate/deps.py` — pure resolver: `FeatureRecord`, `Resolution`, `ref_to_id`, `load_feature_cache`, `resolve`, plus `to_dot` / `to_mermaid` emitters.
- `dvt/tests/test_feature_specs.py`
- `dvt/tests/test_deps.py`
- `dvt/tests/test_feature_deps_command.py`

**Modified:**
- `dvt/src/devtemplate/config.py` — add `feature_specs_dir` + `feature_specs_manifest_path` properties
- `dvt/src/devtemplate/github.py` — add `list_feature_names`, `fetch_feature_spec`
- `dvt/src/devtemplate/cli.py` — `sync` also calls `sync_feature_specs`
- `dvt/src/devtemplate/cli_output_schemas.py` — `SyncOutput` gains `feature_specs: list[str]`
- `dvt/src/devtemplate/commands/feature.py` — `list` column, `show` tree, new `deps` command, `add` message + sidecar field
- `dvt/tests/` — existing `test_sync*.py`, `test_feature*.py` updated for the new column / output key
- `dvt/CHANGELOG.md`, `dvt/README.md`, `dvt/docs/content/quickstart.md` (+ command-reference page if present)

---

## Task 1: GitHub fetchers for feature specs

**Files:**
- Modify: `dvt/src/devtemplate/github.py`
- Test: `dvt/tests/test_github.py` (extend if it exists; else add cases to wherever `fetch_template` is tested)

**Interfaces:**
- Produces:
  - `list_feature_names(client: httpx.Client, repo: str, branch: str) -> Result[list[str], Exception]` — sorted `features/` subdirectory names.
  - `fetch_feature_spec(client: httpx.Client, repo: str, branch: str, feature_id: str) -> Result[dict[str, Any], Exception]` — parsed `devcontainer-feature.json`.
- Both decorated with the module's standard `@on_err(...)` retry. Added to `__all__`.

- [ ] **Step 1: Write failing tests**

Add to the github-fetcher test module (mirror the existing `fetch_template` / `list_template_names` tests — use their mocking style, likely `respx` or a `httpx.MockTransport`; match it):

```python
def test_list_feature_names_returns_sorted_dirs(mock_github):  # adapt fixture name
    mock_github.get(
        "https://api.github.com/repos/o/r/contents/features?ref=main"
    ).respond(json=[
        {"name": "pixi", "type": "dir"},
        {"name": "homebrew", "type": "dir"},
        {"name": "README.md", "type": "file"},
    ])
    result = list_feature_names(httpx.Client(), "o/r", "main")
    assert result.unwrap() == ["homebrew", "pixi"]


def test_fetch_feature_spec_parses_json(mock_github):
    mock_github.get(
        "https://raw.githubusercontent.com/o/r/main/features/pixi/devcontainer-feature.json"
    ).respond(json={"id": "pixi", "version": "1.0.0"})
    result = fetch_feature_spec(httpx.Client(), "o/r", "main", "pixi")
    assert result.unwrap()["id"] == "pixi"
```

- [ ] **Step 2: Run — expect ImportError / NameError**

Run: `pixi run -e dev pytest tests/test_github.py -k "feature_spec or feature_names" -v`
Expected: FAIL (`cannot import name 'list_feature_names'`).

- [ ] **Step 3: Implement in `github.py`**

Add after `fetch_template` (copy the decorator and `execute(_fetch)` pattern verbatim from `list_template_names` / `fetch_template`):

```python
@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def list_feature_names(
    client: httpx.Client, repo: str, branch: str
) -> Result[list[str], Exception]:
    def _fetch() -> list[str]:
        url = f"https://api.github.com/repos/{repo}/contents/features?ref={branch}"
        response = client.get(url)
        response.raise_for_status()
        entries = response.json()
        return sorted(entry["name"] for entry in entries if entry["type"] == "dir")

    return execute(_fetch)


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def fetch_feature_spec(
    client: httpx.Client, repo: str, branch: str, feature_id: str
) -> Result[dict[str, Any], Exception]:
    def _fetch() -> dict[str, Any]:
        url = (
            f"https://raw.githubusercontent.com/{repo}/{branch}"
            f"/features/{feature_id}/devcontainer-feature.json"
        )
        response = client.get(url)
        response.raise_for_status()
        return cast(dict[str, Any], json.loads(response.text))

    return execute(_fetch)
```

Add both names to `__all__`.

- [ ] **Step 4: Run — expect PASS**

Run: `pixi run -e dev pytest tests/test_github.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/github.py tests/test_github.py
git commit -m "feat(dvt): fetch feature devcontainer-feature.json from GitHub"
```

---

## Task 2: Feature spec cache (`feature_specs.py`)

**Files:**
- Modify: `dvt/src/devtemplate/config.py`
- Create: `dvt/src/devtemplate/feature_specs.py`
- Create: `dvt/tests/test_feature_specs.py`

**Interfaces:**
- `config.Settings` gains:
  - `feature_specs_dir -> Path` = `data_dir / "feature-specs"`
  - `feature_specs_manifest_path -> Path` = `data_dir / "feature_specs_manifest.json"`
- `feature_specs.py` exports:
  - `TRIMMED_KEYS` (module constant) — the keys kept from each spec.
  - `sync_feature_specs(settings: Settings, client: httpx.Client) -> Result[list[str], Exception]` — same contract as `sync_images`: fetch every listed feature, write `feature_specs_dir/<id>.json` holding a trimmed record `{id, version, name, description, dependsOn, installsAfter}` (`dependsOn` normalised to a **sorted list of ref strings**), prune names dropped since the previous manifest, update the manifest. Returns the sorted list of ids.
  - `list_cached_feature_specs(settings: Settings) -> list[str]` — sorted `*.json` stems, `[]` when the dir is absent (no `Result`, mirrors `list_cached_images`).
  - `load_cached_feature_spec(settings: Settings, feature_id: str) -> Result[dict[str, Any], Exception]` — the trimmed record; `FileNotFoundError` when absent.
- Reuses `store.validate_template_name` for id validation (same `^[a-z0-9][a-z0-9-]*$` rule) — import it, do not re-declare.

- [ ] **Step 1: Add the config properties + a test**

In `config.py`, after `features_dir`:

```python
    @property
    def feature_specs_dir(self) -> Path:
        return self.data_dir / "feature-specs"

    @property
    def feature_specs_manifest_path(self) -> Path:
        return self.data_dir / "feature_specs_manifest.json"
```

Add to `dvt/tests/test_config.py` (or wherever `templates_dir` is asserted):

```python
def test_feature_specs_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    s = Settings()
    assert s.feature_specs_dir == tmp_path / "feature-specs"
    assert s.feature_specs_manifest_path == tmp_path / "feature_specs_manifest.json"
```

- [ ] **Step 2: Write failing `test_feature_specs.py`**

Create `dvt/tests/test_feature_specs.py`. Mirror `dvt/tests/test_store.py` / `test_images.py` mocking style (they mock `list_*_names` + `fetch_*`; match exactly). Cases:

```python
def test_sync_writes_trimmed_records(settings, mock_github):
    # features/ lists homebrew + pixi
    # homebrew spec: no dependsOn; pixi spec: dependsOn {".../homebrew": {}}, installsAfter [...]
    ids = sync_feature_specs(settings, httpx.Client()).unwrap()
    assert ids == ["homebrew", "pixi"]
    pixi = json.loads((settings.feature_specs_dir / "pixi.json").read_text())
    assert set(pixi) == {"id", "version", "name", "description", "dependsOn", "installsAfter"}
    assert pixi["dependsOn"] == ["ghcr.io/jesserobertson/devcontainers/homebrew"]  # normalised list


def test_sync_prunes_removed_features(settings, mock_github_second_run):
    # first run caches homebrew + pixi; second run lists only pixi
    sync_feature_specs(settings, httpx.Client()).unwrap()
    ids = sync_feature_specs(settings, httpx.Client()).unwrap()  # second fixture
    assert ids == ["pixi"]
    assert not (settings.feature_specs_dir / "homebrew.json").exists()


def test_load_cached_feature_spec_missing_is_err(settings):
    assert load_cached_feature_spec(settings, "nope").is_err()


def test_list_cached_feature_specs_empty_when_no_dir(settings):
    assert list_cached_feature_specs(settings) == []
```

- [ ] **Step 3: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_specs.py tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: devtemplate.feature_specs`).

- [ ] **Step 4: Implement `feature_specs.py`**

```python
from __future__ import annotations

import json
import shutil
from typing import Any, cast

import httpx
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate.config import Settings
from devtemplate.github import fetch_feature_spec, list_feature_names
from devtemplate.store import validate_template_name

__all__ = [
    "sync_feature_specs",
    "list_cached_feature_specs",
    "load_cached_feature_spec",
]

MANIFEST_KEY = "managed_feature_specs"
TRIMMED_KEYS = ("id", "version", "name", "description", "dependsOn", "installsAfter")


def _normalise(spec: dict[str, Any]) -> dict[str, Any]:
    depends_on = spec.get("dependsOn", {})
    if isinstance(depends_on, dict):
        depends_on = sorted(depends_on)
    installs_after = spec.get("installsAfter", [])
    return {
        "id": spec.get("id", ""),
        "version": spec.get("version", ""),
        "name": spec.get("name", ""),
        "description": spec.get("description", ""),
        "dependsOn": list(depends_on),
        "installsAfter": list(installs_after),
    }


@wrap_result
def _read_manifest(settings: Settings) -> list[str]:
    if not settings.feature_specs_manifest_path.exists():
        return []
    data = json.loads(settings.feature_specs_manifest_path.read_text())
    return cast(list[str], data.get(MANIFEST_KEY, []))


@wrap_result
def _write_manifest(settings: Settings, names: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.feature_specs_manifest_path.write_text(
        json.dumps({MANIFEST_KEY: sorted(names)}, indent=2)
    )


@wrap_result
def sync_feature_specs(settings: Settings, client: httpx.Client) -> list[str]:
    """Cache a trimmed devcontainer-feature.json for every features/<id> on
    GitHub. Same prune/manifest contract as devtemplate.images.sync_images."""
    names = list_feature_names(
        client, settings.github_repo, settings.github_branch
    ).unwrap()
    traverse_result(names, validate_template_name).unwrap()

    previous = _read_manifest(settings).unwrap_or([])

    settings.feature_specs_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        spec = fetch_feature_spec(
            client, settings.github_repo, settings.github_branch, name
        ).unwrap()
        (settings.feature_specs_dir / f"{name}.json").write_text(
            json.dumps(_normalise(spec), indent=2)
        )

    for stale in set(previous) - set(names):
        if validate_template_name(stale).is_err():
            continue
        stale_file = settings.feature_specs_dir / f"{stale}.json"
        if stale_file.is_file():
            stale_file.unlink()

    _write_manifest(settings, names).unwrap()
    return names


def list_cached_feature_specs(settings: Settings) -> list[str]:
    if not settings.feature_specs_dir.exists():
        return []
    return sorted(p.stem for p in settings.feature_specs_dir.glob("*.json"))


@wrap_result
def load_cached_feature_spec(settings: Settings, feature_id: str) -> dict[str, Any]:
    validate_template_name(feature_id).unwrap()
    path = settings.feature_specs_dir / f"{feature_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached feature spec named {feature_id!r}. Run 'dvt sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))
```

- [ ] **Step 5: Run — expect PASS**

Run: `pixi run -e dev pytest tests/test_feature_specs.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/config.py src/devtemplate/feature_specs.py tests/test_feature_specs.py tests/test_config.py
git commit -m "feat(dvt): cache trimmed feature specs beside templates and images"
```

---

## Task 3: The resolver (`deps.py`)

**Files:**
- Create: `dvt/src/devtemplate/deps.py`
- Create: `dvt/tests/test_deps.py`

**Interfaces:**
- `ref_to_id(ref: str) -> str` — `"ghcr.io/jesserobertson/devcontainers/pixi:latest"` → `"pixi"`. Strips `@…` first, then `:tag`, then takes `rsplit("/", 1)[-1]`.
- `@dataclass(frozen=True) FeatureRecord` — `id: str`, `version: str`, `name: str`, `description: str`, `depends_on: tuple[str, ...]` (refs), `installs_after: tuple[str, ...]` (refs).
- `@dataclass(frozen=True) Resolution` — `feature: str`, `pulls_in: tuple[str, ...]` (transitive `dependsOn` closure as **ids** where resolvable, else bare refs; deduped; sorted), `installs_after: tuple[str, ...]` (the feature's own `installsAfter`, as ids where resolvable; not transitive).
- `load_feature_cache(settings: Settings) -> dict[str, FeatureRecord]` — every `feature_specs_dir/*.json` keyed by id. `{}` when the dir is absent.
- `resolve(feature_id: str, cache: Mapping[str, FeatureRecord]) -> Result[Resolution, Exception]` — DFS over `depends_on`. Cycle → `Err(ValueError("dependency cycle: a -> b -> a"))`. A `feature_id` not in `cache` → `Err(KeyError(...))`. A dependency ref whose id is not in `cache` is included in `pulls_in` as its bare ref and not recursed.
- `to_dot(resolutions: Iterable[Resolution]) -> str`, `to_mermaid(resolutions: Iterable[Resolution]) -> str` — directed graph, `A -> B` meaning "A pulls in B". Deterministic ordering.
- `__all__` lists all of the above.

- [ ] **Step 1: Write `test_deps.py` (failing)**

```python
from __future__ import annotations

import pytest

from devtemplate.deps import FeatureRecord, Resolution, ref_to_id, resolve

PREFIX = "ghcr.io/jesserobertson/devcontainers"


def rec(id_, depends=(), after=()):
    return FeatureRecord(
        id=id_, version="1.0.0", name=id_, description="",
        depends_on=tuple(f"{PREFIX}/{d}" for d in depends),
        installs_after=tuple(f"{PREFIX}/{a}" for a in after),
    )


def test_ref_to_id_strips_tag_and_path():
    assert ref_to_id(f"{PREFIX}/pixi:latest") == "pixi"
    assert ref_to_id(f"{PREFIX}/pixi@sha256:abc") == "pixi"
    assert ref_to_id(f"{PREFIX}/pixi") == "pixi"


def test_direct_dependency():
    cache = {"pixi": rec("pixi"), "rapids": rec("rapids", depends=["pixi"])}
    r = resolve("rapids", cache).unwrap()
    assert r.pulls_in == ("pixi",)


def test_transitive_and_dedup_diamond():
    cache = {
        "homebrew": rec("homebrew"),
        "shell-kit": rec("shell-kit", depends=["homebrew"]),
        "pixi": rec("pixi", after=["homebrew", "shell-kit"]),
        "big": rec("big", depends=["shell-kit", "pixi"]),
    }
    r = resolve("big", cache).unwrap()
    assert r.pulls_in == ("homebrew", "pixi", "shell-kit")  # sorted, deduped
    # pixi's installsAfter is NOT hoisted into big's closure
    assert "homebrew" not in resolve("pixi", cache).unwrap().pulls_in


def test_installs_after_is_annotation_only():
    cache = {"homebrew": rec("homebrew"), "pixi": rec("pixi", after=["homebrew"])}
    r = resolve("pixi", cache).unwrap()
    assert r.pulls_in == ()
    assert r.installs_after == ("homebrew",)


def test_cycle_is_err():
    cache = {
        "a": rec("a", depends=["b"]),
        "b": rec("b", depends=["a"]),
    }
    assert resolve("a", cache).is_err()


def test_unknown_dependency_ref_kept_bare():
    cache = {"x": rec("x", depends=["../features/common-utils"])}
    # depends_on ref that ref_to_id maps to an id absent from cache
    r = resolve("x", cache).unwrap()
    assert r.pulls_in == ("common-utils",) or r.pulls_in == (
        "ghcr.io/jesserobertson/devcontainers/common-utils",
    )


def test_unknown_feature_is_err():
    assert resolve("ghost", {}).is_err()
```

- [ ] **Step 2: Run — expect ImportError**

Run: `pixi run -e dev pytest tests/test_deps.py -v`
Expected: FAIL (`ModuleNotFoundError: devtemplate.deps`).

- [ ] **Step 3: Implement `deps.py`**

```python
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from logerr import Result
from logerr.utilities import execute

from devtemplate.config import Settings

__all__ = [
    "FeatureRecord",
    "Resolution",
    "ref_to_id",
    "load_feature_cache",
    "resolve",
    "to_dot",
    "to_mermaid",
]


def ref_to_id(ref: str) -> str:
    body = ref.split("@", 1)[0]
    body = body.rsplit(":", 1)[0] if ":" in body.rsplit("/", 1)[-1] else body
    return body.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class FeatureRecord:
    id: str
    version: str
    name: str
    description: str
    depends_on: tuple[str, ...]
    installs_after: tuple[str, ...]


@dataclass(frozen=True)
class Resolution:
    feature: str
    pulls_in: tuple[str, ...]
    installs_after: tuple[str, ...]


def load_feature_cache(settings: Settings) -> dict[str, FeatureRecord]:
    directory = settings.feature_specs_dir
    if not directory.exists():
        return {}
    cache: dict[str, FeatureRecord] = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text())
        cache[path.stem] = FeatureRecord(
            id=data.get("id", path.stem),
            version=data.get("version", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            depends_on=tuple(data.get("dependsOn", [])),
            installs_after=tuple(data.get("installsAfter", [])),
        )
    return cache


def _label(ref: str, cache: Mapping[str, FeatureRecord]) -> str:
    ident = ref_to_id(ref)
    return ident if ident in cache else ref


def resolve(
    feature_id: str, cache: Mapping[str, FeatureRecord]
) -> Result[Resolution, Exception]:
    def _resolve() -> Resolution:
        if feature_id not in cache:
            raise KeyError(f"unknown feature: {feature_id!r}")

        seen: set[str] = set()
        stack: list[str] = []

        def walk(ident: str) -> None:
            if ident in stack:
                cycle = " -> ".join([*stack, ident])
                raise ValueError(f"dependency cycle: {cycle}")
            record = cache.get(ident)
            if record is None:
                return
            stack.append(ident)
            for ref in record.depends_on:
                label = _label(ref, cache)
                seen.add(label)
                walk(label)
            stack.pop()

        walk(feature_id)

        installs_after = tuple(
            _label(ref, cache) for ref in cache[feature_id].installs_after
        )
        return Resolution(
            feature=feature_id,
            pulls_in=tuple(sorted(seen)),
            installs_after=installs_after,
        )

    return execute(_resolve)


def _edges(resolutions: Iterable[Resolution]) -> list[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for res in resolutions:
        for dep in res.pulls_in:
            edges.add((res.feature, dep))
    return sorted(edges)


def to_dot(resolutions: Iterable[Resolution]) -> str:
    lines = ["digraph deps {"]
    for src, dst in _edges(resolutions):
        lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(resolutions: Iterable[Resolution]) -> str:
    lines = ["graph TD"]
    for src, dst in _edges(resolutions):
        lines.append(f"  {src} --> {dst}")
    return "\n".join(lines)
```

Note the `walk` recursion pushes the **label** (id when known); `test_transitive_and_dedup_diamond` expects sorted dedup and that a node's own `installsAfter` never enters a parent's `pulls_in` (it doesn't — only `depends_on` is walked).

- [ ] **Step 4: Run — expect PASS**

Run: `pixi run -e dev pytest tests/test_deps.py -v`
Expected: PASS. If `test_unknown_dependency_ref_kept_bare` fails on the label form, tighten the assertion in the test to the single form `ref_to_id` actually yields for that input and keep that.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/deps.py tests/test_deps.py
git commit -m "feat(dvt): pure dependsOn resolver with dot/mermaid emitters"
```

---

## Task 4: Wire spec sync into `dvt sync`

**Files:**
- Modify: `dvt/src/devtemplate/cli.py` (the `sync` command, ~line 94-140)
- Modify: `dvt/src/devtemplate/cli_output_schemas.py` (`SyncOutput`)
- Modify: existing sync tests (`dvt/tests/test_cli.py` / `test_sync*.py`)

**Interfaces:**
- `sync`'s `do_sync` also calls `sync_feature_specs(settings, client).unwrap()` and returns `{"features": ..., "images": ..., "feature_specs": ...}`.
- `SyncOutput` gains `feature_specs: list[str]`.
- Human output gains a third line: `Synced N feature specs: a, b, c`.

- [ ] **Step 1: Update `SyncOutput` + its test**

In `cli_output_schemas.py`:

```python
class SyncOutput(BaseModel):
    ok: Literal[True]
    features: list[str]
    images: list[str]
    feature_specs: list[str]
```

Find the test asserting `sync --json` shape and add `feature_specs` to the expected keys (make it FAIL first by running it).

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/ -k "sync" -v`
Expected: FAIL — schema/`do_sync` mismatch, or the `--json` shape test.

- [ ] **Step 3: Edit `cli.py`**

Add import: `from devtemplate.feature_specs import sync_feature_specs`. In `do_sync`:

```python
    @wrap_result
    def do_sync(_status: object) -> dict[str, list[str]]:
        with httpx.Client() as client:
            features = sync_templates(settings, client).unwrap()
            images = sync_images(settings, client).unwrap()
            feature_specs = sync_feature_specs(settings, client).unwrap()
        return {"features": features, "images": images, "feature_specs": feature_specs}
```

And the human branch:

```python
        lambda: console.print(
            f"Synced {len(synced['features'])} features: "
            f"{', '.join(synced['features'])}\n"
            f"Synced {len(synced['images'])} images: "
            f"{', '.join(synced['images'])}\n"
            f"Synced {len(synced['feature_specs'])} feature specs: "
            f"{', '.join(synced['feature_specs'])}"
        ),
```

- [ ] **Step 4: Update `dvt feature add`'s inline auto-sync**

`commands/feature.py` `add` currently calls only `sync_templates` when the cache is empty (line ~206-217). Change `do_sync` there to also run `sync_feature_specs` so a first-run `dvt feature add` populates the dep cache too:

```python
        def do_sync(_status: object) -> Result[list[str], Exception]:
            with httpx.Client() as client:
                templates = sync_templates(settings, client)
                if templates.is_err():
                    return templates
                sync_feature_specs(settings, client)  # best-effort; dep info only
                return templates
```

Add the import at the top of `feature.py`.

- [ ] **Step 5: Run — expect PASS**

Run: `pixi run -e dev pytest tests/ -k "sync or feature_add or feature add" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/cli.py src/devtemplate/cli_output_schemas.py src/devtemplate/commands/feature.py tests/
git commit -m "feat(dvt): dvt sync also caches feature dependency specs"
```

---

## Task 5: "Pulls in" column in `dvt feature list`

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py` (`list_features`)
- Modify: `dvt/tests/test_feature*.py` (list tests)

**Interfaces:**
- Table gains a `"Pulls in"` column between `"Description"` and `"Base Image"`. Value = `", ".join(resolve(name, cache).unwrap().pulls_in)` or `"—"` when empty / the name is absent from `cache` / `resolve` errors.
- `--json` rows gain `"pulls_in": list[str]` (`[]` when none/unavailable).
- When `load_feature_cache` returns `{}` (never synced): every row's column is `"—"`, no error, and a single dim stderr hint `run 'dvt sync' for dependency info` is printed once (non-JSON only).

- [ ] **Step 1: Write failing tests**

In the feature-list test module:

```python
def test_list_shows_pulls_in_column(synced_cache_with_deps, capsys):
    # cache: rapids dependsOn pixi
    list_features(json_output=False)
    out = capsys.readouterr().out
    assert "Pulls in" in out
    # rapids row mentions pixi
    assert "pixi" in [line for line in out.splitlines() if "rapids" in line][0]


def test_list_json_has_pulls_in(synced_cache_with_deps, capsys):
    list_features(json_output=True)
    rows = json.loads(capsys.readouterr().out)
    rapids = next(r for r in rows if r["name"] == "rapids")
    assert rapids["pulls_in"] == ["pixi"]


def test_list_without_feature_cache_degrades(synced_templates_only, capsys):
    list_features(json_output=False)
    out = capsys.readouterr().out
    assert "Pulls in" in out and "—" in out
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/ -k "pulls_in or list_without_feature_cache" -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `list_features`**

Add imports: `from devtemplate.deps import load_feature_cache, resolve`.

After loading `settings` and before the row loop:

```python
    feature_cache = load_feature_cache(settings)
```

In the per-name loop, compute:

```python
        pulls_in: list[str] = []
        if name in feature_cache:
            match resolve(name, feature_cache):
                case Ok(resolution):
                    pulls_in = list(resolution.pulls_in)
                case Err(_):
                    pulls_in = []
        rows.append({..., "pulls_in": pulls_in})   # add to the existing dict
```

JSON branch: unchanged (`rows` already carries `pulls_in`).

Table branch:

```python
    table = Table("Name", "Description", "Pulls in", "Base Image")
    for row in rows:
        table.add_row(
            row["name"],
            row["description"],
            ", ".join(row["pulls_in"]) or "—",
            row["image"],
        )
    console.print(table)
    if not feature_cache:
        stderr_console.print("[dim]run 'dvt sync' for dependency info[/dim]")
```

- [ ] **Step 4: Run — expect PASS + no regressions**

Run: `pixi run -e dev pytest tests/ -k "feature and list" -v`
Expected: PASS. Update any existing list test that asserted an exact 3-column header/row count.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/
git commit -m "feat(dvt): dvt feature list shows a Pulls in column"
```

---

## Task 6: Dependency tree in `dvt feature show`

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py` (`show_feature`)
- Modify: feature-show tests

**Interfaces:**
- Non-JSON: after printing the overlay JSON, if `name in load_feature_cache(settings)`, print a `rich.tree.Tree` rooted at `name`; children are `dependsOn` recursively (id labels), each node suffixed `  (after: x, y)` in dim when that record has `installsAfter`. If the name isn't in the cache, print nothing extra (overlay JSON stays the whole output — preserves the current contract that success output is "always the cached feature's raw devcontainer.json overlay"; the tree is additive and only for non-JSON).
- `--json`: the emitted object gains `"resolved_depends_on": list[str]` (the `pulls_in` closure) alongside the existing raw overlay. Existing consumers keying other fields are unaffected.

- [ ] **Step 1: Failing tests**

```python
def test_show_prints_dependency_tree(synced_cache_with_deps, capsys):
    show_feature(name="rapids", json_output=False)
    out = capsys.readouterr().out
    assert "rapids" in out and "pixi" in out


def test_show_json_has_resolved_depends_on(synced_cache_with_deps, capsys):
    show_feature(name="rapids", json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_depends_on"] == ["pixi"]


def test_show_unknown_in_cache_still_prints_overlay(synced_templates_only, capsys):
    show_feature(name="cli", json_output=False)
    out = capsys.readouterr().out
    assert '"features"' in out  # overlay still printed, no crash
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/ -k "show and (tree or resolved_depends_on or overlay)" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `from rich.tree import Tree` and reuse the `deps` imports. Build a helper in `feature.py`:

```python
def _dep_tree(feature_id: str, cache: Mapping[str, FeatureRecord]) -> Tree:
    def node(ident: str, parent: Tree) -> None:
        record = cache.get(ident)
        suffix = ""
        if record and record.installs_after:
            labels = ", ".join(ref_to_id(r) for r in record.installs_after)
            suffix = f" [dim](after: {labels})[/dim]"
        branch = parent.add(f"{ident}{suffix}")
        for ref in (record.depends_on if record else ()):
            node(ref_to_id(ref), branch)

    root = Tree(feature_id)
    for ref in cache[feature_id].depends_on:
        node(ref_to_id(ref), root)
    return root
```

In `show_feature`, after `print(json.dumps(template, indent=2))`:

```python
    cache = load_feature_cache(settings)
    if json_output:
        # re-emit with the extra key (show currently prints the overlay raw;
        # for --json, merge resolved_depends_on in)
        ...
    elif name in cache:
        console.print(_dep_tree(name, cache))
```

For `--json`: the current code does `print(json.dumps(template, indent=2))` unconditionally. Change so that when `json_output` and `name in cache`, it prints `json.dumps({**template, "resolved_depends_on": list(resolve(name, cache).unwrap_or(Resolution(name, (), ())).pulls_in)}, indent=2)` instead. When `name not in cache`, keep the raw overlay.

- [ ] **Step 4: Run — expect PASS**

Run: `pixi run -e dev pytest tests/ -k "feature and show" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/
git commit -m "feat(dvt): dvt feature show renders the dependency tree"
```

---

## Task 7: `dvt feature deps` command

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py` (new `@app.command("deps")`, alias `tree`)
- Create: `dvt/tests/test_feature_deps_command.py`
- Modify: `dvt/src/devtemplate/cli_output_schemas.py` if the repo registers per-command output schemas (there is a `cli_output_schemas` map keyed by command name — add `"feature deps"` / `"deps"` following the existing key convention)

**Interfaces:**
- `dvt feature deps [NAME] [--format tree|dot|mermaid] [--json]`.
  - `NAME` given: resolve that one feature (fuzzy-matched via the existing `@fuzzy_argument` / `resolve_or_confirm` pattern used by `show`).
  - No `NAME`: resolve every id in `list_cached_feature_specs(settings)`.
  - `--format tree` (default): Rich tree(s) — reuse `_dep_tree`; for the whole-fleet case print one tree per feature that has any `depends_on`.
  - `--format dot`: `deps.to_dot(resolutions)` to stdout.
  - `--format mermaid`: `deps.to_mermaid(resolutions)` to stdout.
  - `--json`: `{ "<id>": { "pulls_in": [...], "installs_after": [...] }, ... }` (one key for a single NAME, all for the fleet).
  - Empty cache → exit 0 with `No feature dependency data. Run 'dvt sync' first.` on stderr (non-JSON) / `{}` (JSON).
  - Unknown `NAME` (after fuzzy) → `unwrap_or_exit` on the `resolve` `Err`, same as `show`.

- [ ] **Step 1: Write `test_feature_deps_command.py` (failing)**

```python
import json

from devtemplate.commands.feature import deps as deps_cmd  # the Typer callback


def test_deps_single_tree(synced_cache_with_deps, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=False)
    out = capsys.readouterr().out
    assert "rapids" in out and "pixi" in out


def test_deps_single_json(synced_cache_with_deps, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"rapids": {"pulls_in": ["pixi"], "installs_after": []}}


def test_deps_fleet_dot(synced_cache_with_deps, capsys):
    deps_cmd(name=None, fmt="dot", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("digraph deps {")
    assert '"rapids" -> "pixi";' in out


def test_deps_fleet_mermaid(synced_cache_with_deps, capsys):
    deps_cmd(name=None, fmt="mermaid", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("graph TD")
    assert "rapids --> pixi" in out


def test_deps_empty_cache(synced_templates_only, capsys):
    deps_cmd(name=None, fmt="tree", json_output=True)
    assert json.loads(capsys.readouterr().out) == {}
```

(Match the actual parameter names/typer option spelling to the other commands in `feature.py` — `json_output` is the established name; pick `fmt` with `"--format"` since `format` shadows a builtin, mirroring any similar option elsewhere in the codebase.)

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_deps_command.py -v`
Expected: FAIL (`ImportError: cannot import name 'deps'`).

- [ ] **Step 3: Implement the command in `feature.py`**

```python
@app.command("deps")
@app.command("tree", hidden=True)  # alias
def deps(
    name: str | None = typer.Argument(None, help="Feature to inspect; omit for the whole fleet."),  # noqa: B008
    fmt: str = typer.Option("tree", "--format", help="tree | dot | mermaid"),  # noqa: B008
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON."),  # noqa: B008
) -> None:
    """Show what each feature pulls in via dependsOn."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    cache = load_feature_cache(settings)

    if not cache:
        if json_output:
            print(json.dumps({}))
        else:
            stderr_console.print("No feature dependency data. Run 'dvt sync' first.")
        raise typer.Exit(code=0)

    if name is not None:
        resolved = unwrap_or_exit(
            resolve_or_confirm(
                name, list_cached_feature_specs(settings), label="feature",
                assume_yes=json_output, interactive=not json_output,
            ),
            console, json_output=json_output,
        )
        targets = [resolved]
    else:
        targets = list_cached_feature_specs(settings)

    resolutions = []
    for target in targets:
        resolutions.append(unwrap_or_exit(resolve(target, cache), console, json_output=json_output))

    if json_output:
        print(json.dumps({
            r.feature: {"pulls_in": list(r.pulls_in), "installs_after": list(r.installs_after)}
            for r in resolutions
        }))
        return

    if fmt == "dot":
        print(to_dot(resolutions))
    elif fmt == "mermaid":
        print(to_mermaid(resolutions))
    else:
        for r in resolutions:
            if r.pulls_in:
                console.print(_dep_tree(r.feature, cache))
```

Add imports for `to_dot`, `to_mermaid`, `list_cached_feature_specs`. If `@app.command` twice on one function isn't how this Typer wrapper does aliases, register a second thin `tree` command that calls `deps(...)`.

- [ ] **Step 4: Run — expect PASS**

Run: `pixi run -e dev pytest tests/test_feature_deps_command.py -v`
Expected: PASS.

- [ ] **Step 5: Fuzzy-injection / describe regression check**

Run: `pixi run -e dev pytest tests/ -k "describe or fuzzy" -v`
Expected: PASS — the new subcommand must not break `--describe` scoping (per memory `project_dvt_scoped_describe`) or the fuzzy `--yes` injection tests.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/commands/feature.py src/devtemplate/cli_output_schemas.py tests/test_feature_deps_command.py
git commit -m "feat(dvt): add dvt feature deps (tree/dot/mermaid/json)"
```

---

## Task 8: "also pulling in" message at `dvt feature add`

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py` (`add_one` / `add`)
- Modify: feature-add tests

**Interfaces:**
- In `add`, after `resolved` is known and before/after `add_one` succeeds: compute `resolve(resolved, load_feature_cache(settings))`. If `pulls_in` non-empty and not `json_output`, print `also pulling in: <a, b> (via dependsOn)` to `console`.
- `add_one` writes `"pulls_in": list[str]` onto the sidecar `applied` entry it appends (currently `{"name": name, "overlay": overlay}` → add the key). `[]` when unknown/none.
- `devcontainer.json` itself is **not** modified for implied deps (explicit non-goal). The merge/overlay logic is untouched.
- JSON output of `add` unchanged (`FeatureAddOutput` still just `{ok, added}`).

- [ ] **Step 1: Failing tests**

```python
def test_add_reports_pulled_in_deps(project_with_devcontainer, synced_cache_with_deps, capsys):
    add(names=["rapids"], assume_yes=True, json_output=False)
    out = capsys.readouterr().out
    assert "also pulling in" in out and "pixi" in out


def test_add_records_pulls_in_on_sidecar(project_with_devcontainer, synced_cache_with_deps):
    add(names=["rapids"], assume_yes=True, json_output=False)
    sidecar = json.loads((project_with_devcontainer / ".devcontainer" / "dvt-features.json").read_text())
    entry = next(e for e in sidecar["applied"] if e["name"] == "rapids")
    assert entry["pulls_in"] == ["pixi"]


def test_add_does_not_inject_pixi_into_devcontainer_json(project_with_devcontainer, synced_cache_with_deps):
    add(names=["rapids"], assume_yes=True, json_output=False)
    cfg = json.loads((project_with_devcontainer / ".devcontainer" / "devcontainer.json").read_text())
    feature_keys = list(cfg["features"])
    assert not any("pixi" in k for k in feature_keys)  # only rapids' own ref was added
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/ -k "add and (pulled_in or pulls_in or inject)" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `add_one`, change the append:

```python
    sidecar["applied"].append({"name": name, "overlay": overlay, "pulls_in": pulls_in})
```

Pass `pulls_in` into `add_one` (new keyword arg, default `()`), computed by the caller. In `add`, after resolving `resolved`:

```python
        feature_cache = load_feature_cache(settings)
        pulls_in = []
        if resolved in feature_cache:
            match resolve(resolved, feature_cache):
                case Ok(resolution):
                    pulls_in = list(resolution.pulls_in)
                case Err(_):
                    pulls_in = []
        # ... existing add_one(...) call, now add_one(resolved, ..., pulls_in=pulls_in)
        if pulls_in and not json_output:
            console.print(f"also pulling in: {', '.join(pulls_in)} (via dependsOn)")
```

Keep `remove_one` tolerant of the new `pulls_in` key (it iterates `applied` by `name`; the extra key is inert — no change needed, but add a one-line test that `remove` still works on an entry carrying `pulls_in`).

- [ ] **Step 4: Run — expect PASS**

Run: `pixi run -e dev pytest tests/ -k "feature and (add or remove)" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/
git commit -m "feat(dvt): report dependsOn pull-ins at feature add time"
```

---

## Task 9: Docs, version bump, full verification

**Files:**
- Modify: `dvt/CHANGELOG.md`, `dvt/README.md`, `dvt/docs/content/quickstart.md` (+ the command-reference doc if one exists — check `dvt/docs/content/`)
- Modify: `dvt/pyproject.toml` (`version = "0.5.0"`)

- [ ] **Step 1: `dvt/pyproject.toml` version**

Set `version = "0.5.0"`.

- [ ] **Step 2: `dvt/CHANGELOG.md`**

New `## [0.5.0]` section: `dvt sync` now also caches every feature's `devcontainer-feature.json`; `dvt feature list` gains a "Pulls in" column; `dvt feature show` renders the dependency tree; new `dvt feature deps` command with `--format tree|dot|mermaid` and `--json`; `dvt feature add` reports what a feature pulls in via `dependsOn` and records it in the sidecar. Note: `dvt` does not inject implied features into `devcontainer.json` — the devcontainer build applies `dependsOn` itself.

- [ ] **Step 3: `dvt/README.md` + quickstart**

- Add `dvt feature deps [name]` to the command list in `dvt/README.md`'s Usage block and the `dvt/docs/content/quickstart.md` reference.
- Mention the "Pulls in" column under `dvt feature list`.
- One line: after upgrading, run `dvt sync` once to populate dependency info (older caches show `—`).

- [ ] **Step 4: Full dvt suite + quality gate**

Run: `pixi run -e dev quality check` then `pixi run -e dev test all`
Expected: all green (mypy + ruff + full pytest). Fix any type/lint fallout in the new modules (`deps.py`, `feature_specs.py`) — they must pass mypy with the repo's settings and carry `from __future__ import annotations` + explicit `__all__` per the underscore-audit convention.

- [ ] **Step 5: Docs build (if the repo checks it)**

Run: `pixi run -e dev docs build --strict`
Expected: PASS (no broken links from the new command references).

- [ ] **Step 6: Commit + push**

```bash
git add dvt/pyproject.toml dvt/CHANGELOG.md dvt/README.md dvt/docs/
git commit -m "docs(dvt): document feature dependency awareness; bump to 0.5.0"
git push -u origin HEAD
```

- [ ] **Step 7: After merge — release tag**

Push `dvt-v0.5.0` once merged to `main` (triggers `publish-dvt.yml` TestPyPI + `release-dvt.yml`), per the repo's existing release flow. Real-PyPI publish stays a manual `workflow_dispatch`.

---

## Self-Review

**1. Spec coverage** (§"`dvt` — dependency awareness"):

| Spec item | Task |
|---|---|
| `github.py` `list_feature_names` + `fetch_feature_spec` | Task 1 |
| `config.py` `feature_specs_dir` (spec said `features_dir`; renamed — `features_dir` is already the OCI pull cache, noted in Global Constraints) | Task 2 |
| `store.py` `sync_feature_specs` + trimmed record + `managed_features` manifest key | Task 2 (as `feature_specs.py`; manifest key `managed_feature_specs`) |
| `dependsOn` object→list normalisation | Task 2 `_normalise` |
| Request-count / abort-on-failure contract | Task 2 (mirrors `sync_images`: `.unwrap()` per fetch aborts) |
| `deps.py` `FeatureRecord` / `Resolution` / `load_feature_cache` / `resolve` | Task 3 |
| DFS closure, cycle → `Err`, unknown ref kept bare, `installsAfter` annotation-only | Task 3 tests |
| `ref_to_id` mapping | Task 3 |
| `feature list` "Pulls in" column + `--json` `pulls_in` + `—` degradation | Task 5 |
| `feature show` tree + `--json` `resolved_depends_on` | Task 6 |
| `feature deps` command + `--format tree/dot/mermaid` + `--json` + fleet mode | Task 7 |
| `feature add` message + sidecar `pulls_in`, no `devcontainer.json` injection | Task 8 |
| Degradation: never hard-fail `list`/`add` on empty cache | Tasks 5, 8 (explicit `if not feature_cache` / `if resolved in feature_cache`) |
| `sync` wiring + `SyncOutput` | Task 4 |
| Parallelising sync is out of scope | not attempted (Global Constraints echo the spec non-goal) |
| Versioning & docs | Task 9 |

**2. Placeholder scan:** All code steps carry literal content. The two soft spots are called out as real instructions, not placeholders: Task 1/2/5/6/7/8 say "match the existing test/mocking/fuzzy style" — the executor must read the neighbouring module first, which is a genuine step. Task 6's `--json` branch describes the exact merged-dict expression. Task 7 notes the alias-registration may need a second thin command depending on how the Typer wrapper handles double `@app.command` — with the concrete fallback stated.

**3. Type/name consistency:**
- `feature_specs_dir` / `feature_specs_manifest_path` defined once (Task 2), used in Tasks 3, 7.
- `FeatureRecord` fields (`id, version, name, description, depends_on, installs_after`) identical in Task 3's definition, its tests, and `load_feature_cache`. `depends_on`/`installs_after` are `tuple[str, ...]` of **refs**; `Resolution.pulls_in`/`installs_after` are `tuple[str, ...]` of **ids/labels** — the asymmetry is intentional and stated in both the Interfaces block and the resolver body.
- `resolve(feature_id, cache) -> Result[Resolution, Exception]` — same signature in Task 3, 5, 6, 7, 8.
- `ref_to_id` / `to_dot` / `to_mermaid` / `_dep_tree` names consistent across Tasks 3, 6, 7.
- `_normalise` output keys (`dependsOn` as a list) match what `load_feature_cache` reads (`data.get("dependsOn", [])`) — Task 2 writes, Task 3 reads.
- CLI option name `json_output` (not `json`) matches every existing command in `feature.py`; new `--format` bound to param `fmt` to avoid shadowing `format`.
- Sidecar entry shape `{"name", "overlay", "pulls_in"}` — written in Task 8 `add_one`, read in Task 8's sidecar test; `remove_one` matches on `name` only, unaffected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-dvt-dependency-awareness.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

**Sequencing note:** Plan 1 (`2026-09-02-shell-cli-features.md`) should land first — its features are what give `dvt sync` real `dependsOn` edges to cache. Plan 2's resolver and tests run fine against fixture specs regardless, so the two can be developed in parallel, but Plan 2's integration value depends on Plan 1's `dependsOn` blocks being published.

Which approach?
