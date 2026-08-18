# dvt: image registry + fuzzy name matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `dvt image` subcommand (sync/list/show/create/update/delete) for the two base images this repo builds, and a reusable fuzzy-name-matching capability wired into `dvt feature add/remove/show`, `dvt image show/update/delete`, and `dvt init --image`.

**Architecture:** A generic `resolve_or_confirm` primitive (stdlib `difflib` + `typer.confirm`) backs two consumers: a `fuzzy_argument` decorator for the common "resolve this argument to one of a known list of names" case, and a bespoke `resolve_image_ref` in `images.py` for the richer name-or-alias-or-literal-ref case `dvt init --image` needs. The image registry itself (`images/*.json` in this repo) is fetched read-only from GitHub exactly like `dvt feature` already fetches `templates/`; writes (`create`/`update`/`delete`) edit that same repo's working tree directly, with no GitHub API write access at all.

**Tech Stack:** Python 3.12+, Typer, Rich, `logerr` (Result/Ok/Err), `httpx`, stdlib `difflib`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-18-dvt-image-registry-and-fuzzy-match-design.md`

## Global Constraints

- `match`/`case` wherever branching naturally maps to it (consuming `Ok`/`Err`, branching on list length), not `if`/`elif` chains — matches this codebase's existing convention (see `commands/feature.py`'s `list_features`).
- Every new/changed typer argument and option must carry non-empty `help=` text — `tests/test_cli_help.py` (in `dvt/tests/`) enforces this across the whole CLI tree and will fail otherwise.
- Every `--json`-capable command's success payload must have a matching Pydantic model registered in `cli_output_schemas.OUTPUT_MODELS`, keyed by the same dotted name `describe_app` produces (e.g. `"image list"`).
- Non-interactive mode (any `--json` invocation) never calls `typer.confirm` — it always resolves to either an unambiguous match or an `Err` carrying the suggestion in its message, so scripted use never hangs. (This plan keys "interactive" purely off `--json`, not raw TTY detection — see Task 1's note.)
- No GitHub API write calls anywhere in this feature — `dvt image create/update/delete` only ever touch a local git working tree found by walking up from `cwd` for a `.git` directory.
- All new/changed Python files live under `dvt/src/devtemplate/` or `dvt/tests/`, run via `pixi run pytest` from `dvt/` (per `dvt/README.md`), except the two new registry JSON files (`images/*.json`, at the repo root, sibling to `templates/`) and the `tests/test_static.py` addition (repo-root test suite, run via plain `pytest` from the repo root).

---

### Task 1: `fuzzy.py` — `resolve_or_confirm` primitive

**Files:**
- Create: `dvt/src/devtemplate/fuzzy.py`
- Test: `dvt/tests/test_fuzzy.py`

**Interfaces:**
- Produces: `resolve_or_confirm(query: str, candidates: list[str], *, label: str, assume_yes: bool = False, interactive: bool = True) -> Result[str, Exception]`

Behavior: exact match in `candidates` → `Ok(query)`, no prompt. Otherwise `difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)`: empty → `Err` listing known candidates; one match + `assume_yes` → `Ok(match)`; one match + not `interactive` → `Err` naming the suggested match (never prompts); one match + interactive → `typer.confirm(...)`, yes → `Ok(match)`, no → `Err("Aborted: ...")`.

- [ ] **Step 1: Write the failing tests**

```python
# dvt/tests/test_fuzzy.py
from __future__ import annotations

from devtemplate.fuzzy import resolve_or_confirm


def test_exact_match_passes_through_with_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastapi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_no_close_match_returns_err_listing_candidates():
    result = resolve_or_confirm("zzz-nothing-like-it", ["fastapi", "agent"], label="feature")
    assert result.is_err()
    error = str(result.unwrap_err())
    assert "fastapi" in error
    assert "agent" in error


def test_close_match_confirmed_yes_resolves(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_close_match_confirmed_no_returns_err(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_err()
    assert "fastpi" in str(result.unwrap_err())


def test_assume_yes_skips_the_prompt_entirely(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature", assume_yes=True)
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_non_interactive_close_match_fails_with_suggestion_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature", interactive=False)
    assert result.is_err()
    assert "fastapi" in str(result.unwrap_err())
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `dvt/`): `pixi run pytest tests/test_fuzzy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.fuzzy'`

- [ ] **Step 3: Write the implementation**

```python
# dvt/src/devtemplate/fuzzy.py
from __future__ import annotations

import difflib

import typer
from logerr import Err, Ok, Result

__all__ = ["resolve_or_confirm"]


def resolve_or_confirm(
    query: str,
    candidates: list[str],
    *,
    label: str,
    assume_yes: bool = False,
    interactive: bool = True,
) -> Result[str, Exception]:
    """Resolve `query` against `candidates`.

    An exact match passes through unchanged with no prompt. Otherwise the
    closest candidate (stdlib difflib, cutoff 0.6) is either auto-accepted
    (assume_yes), confirmed interactively via typer.confirm, or - when
    interactive=False, e.g. under --json - reported as a suggestion inside
    a plain Err rather than prompted for, so a script never hangs on an
    unanswerable question. No close match at all is a plain Err listing
    every known candidate.
    """
    if query in candidates:
        return Ok(query)

    matches = difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)
    match matches:
        case []:
            known = ", ".join(sorted(candidates)) or "(none cached)"
            return Err(ValueError(f"No {label} named {query!r}. Known {label}s: {known}"))
        case [match, *_]:
            if assume_yes:
                return Ok(match)
            if not interactive:
                return Err(
                    ValueError(
                        f"No {label} named {query!r}. Did you mean {match!r}? "
                        "Re-run with --yes to accept it, or pass the exact name."
                    )
                )
            if typer.confirm(f"No {label} named '{query}'. Did you mean '{match}'?"):
                return Ok(match)
            return Err(ValueError(f"Aborted: no {label} named {query!r}."))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_fuzzy.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/fuzzy.py dvt/tests/test_fuzzy.py
git commit -m "feat(dvt): add resolve_or_confirm fuzzy-match primitive"
```

---

### Task 2: `fuzzy.py` — `fuzzy_argument` decorator

**Files:**
- Modify: `dvt/src/devtemplate/fuzzy.py`
- Test: `dvt/tests/test_fuzzy.py`

**Interfaces:**
- Consumes: `resolve_or_confirm` (Task 1); `unwrap_or_exit` from `devtemplate.cli_support`; `load_settings` from `devtemplate.config`.
- Produces: `fuzzy_argument(param: str, *, candidates_fn: Callable[[Settings], list[str]], label: str, console: Console) -> Callable[[Callable], Callable]` — a decorator for a typer command function. Injects a standardized `--yes`/`-y` boolean option (kwarg name `assume_yes`) into the wrapped function's signature, and resolves the named `param`'s value (a `str`, or a `list[str]` for a multi-value argument) against `candidates_fn(settings)` before the wrapped function body runs. `candidates_fn` is called with a freshly loaded `Settings` on every invocation - later tasks pass existing lookups like `list_cached_templates`/`list_cached_images` directly, with no wrapper needed since their signature already matches.

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_fuzzy.py`:

```python
import typer
from typer.testing import CliRunner
from rich.console import Console

from devtemplate.fuzzy import fuzzy_argument

runner = CliRunner()


def _greet_app():
    app = typer.Typer()
    console = Console()

    @app.command("greet")
    @fuzzy_argument(
        "names",
        candidates_fn=lambda settings: ["alice", "bob"],
        label="person",
        console=console,
    )
    def greet(
        names: list[str] = typer.Argument(..., help="Name(s) to greet."),
        json_output: bool = typer.Option(False, "--json", help="JSON mode."),
    ) -> None:
        for name in names:
            print(f"hello {name}")

    return app


def test_fuzzy_argument_exact_match_runs_unchanged():
    result = runner.invoke(_greet_app(), ["greet", "alice"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_injects_yes_flag_into_help():
    result = runner.invoke(_greet_app(), ["greet", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "--yes" in result.output
    assert "-y" in result.output


def test_fuzzy_argument_prompts_and_resolves_on_confirm():
    result = runner.invoke(_greet_app(), ["greet", "alise"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_yes_flag_skips_prompt():
    result = runner.invoke(_greet_app(), ["greet", "alise", "--yes"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_json_mode_fails_with_suggestion_no_hang():
    result = runner.invoke(_greet_app(), ["greet", "alise", "--json"])
    assert result.exit_code == 1
    assert "alice" in result.output


def test_fuzzy_argument_resolves_every_item_in_a_multi_value_argument():
    result = runner.invoke(_greet_app(), ["greet", "alice", "bob"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output
    assert "hello bob" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_fuzzy.py -v -k fuzzy_argument`
Expected: FAIL with `ImportError: cannot import name 'fuzzy_argument'`

- [ ] **Step 3: Write the implementation**

Append to `dvt/src/devtemplate/fuzzy.py` (and update its imports/`__all__`):

```python
# add to the top of fuzzy.py, alongside the existing imports:
import functools
import inspect
from typing import Any, Callable

from rich.console import Console

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import Settings, load_settings

# update __all__:
__all__ = ["fuzzy_argument", "resolve_or_confirm"]


def fuzzy_argument(
    param: str,
    *,
    candidates_fn: Callable[[Settings], list[str]],
    label: str,
    console: Console,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for a typer command: fuzzy-resolve `param`'s value (a str,
    or a list[str] for a multi-value argument) against candidates_fn(settings)
    before the wrapped function runs, and inject a standardized --yes/-y
    option (kwarg `assume_yes`) into its signature so every command using
    this decorator gets the same flag name, message format, and exit
    behavior. On no match (or a declined confirmation) this exits via
    unwrap_or_exit before the wrapped function is ever called - it only
    ever sees an already-resolved name (or list of names).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        original_sig = inspect.signature(func)
        yes_param = inspect.Parameter(
            "assume_yes",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                False,
                "--yes",
                "-y",
                help=f"Auto-accept a fuzzy-matched {label} name instead of prompting.",
            ),
            annotation=bool,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            assume_yes = kwargs.pop("assume_yes", False)
            json_output = kwargs.get("json_output", False)
            settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
            candidates = candidates_fn(settings)

            def resolve_one(value: str) -> str:
                result = resolve_or_confirm(
                    value,
                    candidates,
                    label=label,
                    assume_yes=assume_yes,
                    interactive=not json_output,
                )
                return unwrap_or_exit(result, console, json_output=json_output)

            raw = kwargs.get(param)
            kwargs[param] = (
                [resolve_one(value) for value in raw]
                if isinstance(raw, list)
                else resolve_one(raw)
            )
            return func(*args, **kwargs)

        wrapper.__signature__ = original_sig.replace(
            parameters=[*original_sig.parameters.values(), yes_param]
        )
        return wrapper

    return decorator
```

Note: `devtemplate.cli_support` and `devtemplate.config` do not import `devtemplate.fuzzy`, so this doesn't introduce an import cycle.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_fuzzy.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/fuzzy.py dvt/tests/test_fuzzy.py
git commit -m "feat(dvt): add fuzzy_argument decorator for CLI name resolution"
```

---

### Task 3: `config.py` — image cache paths

**Files:**
- Modify: `dvt/src/devtemplate/config.py:47-57` (the `templates_dir`/`features_dir`/`manifest_path` properties)
- Test: `dvt/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.images_dir -> Path` (`data_dir / "images"`), `Settings.image_manifest_path -> Path` (`data_dir / "image_manifest.json"`).

- [ ] **Step 1: Write the failing test**

Add to `dvt/tests/test_config.py`, in `test_settings_paths_derive_from_data_dir`:

```python
def test_settings_paths_derive_from_data_dir(settings, tmp_path):
    assert settings.data_dir == tmp_path
    assert settings.templates_dir == tmp_path / "templates"
    assert settings.manifest_path == tmp_path / "manifest.json"
    assert settings.images_dir == tmp_path / "images"
    assert settings.image_manifest_path == tmp_path / "image_manifest.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_config.py::test_settings_paths_derive_from_data_dir -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'images_dir'`

- [ ] **Step 3: Write the implementation**

In `dvt/src/devtemplate/config.py`, add after the existing `manifest_path` property (line 57):

```python
    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def image_manifest_path(self) -> Path:
        return self.data_dir / "image_manifest.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/config.py dvt/tests/test_config.py
git commit -m "feat(dvt): add images_dir/image_manifest_path to Settings"
```

---

### Task 4: `github.py` — image registry fetch functions

**Files:**
- Modify: `dvt/src/devtemplate/github.py`
- Test: `dvt/tests/test_github.py`

**Interfaces:**
- Produces: `list_image_names(client: httpx.Client, repo: str, branch: str) -> Result[list[str], Exception]` (GitHub Contents API on `images/`, `.json` files only, sorted, suffix stripped); `fetch_image_metadata(client: httpx.Client, repo: str, branch: str, name: str) -> Result[dict[str, Any], Exception]` (raw fetch of `images/<name>.json`).

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_github.py`:

```python
from devtemplate.github import fetch_image_metadata, list_image_names


def test_list_image_names_returns_only_json_files():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"name": "base-ubuntu.json", "type": "file"},
                {"name": "README.md", "type": "file"},
                {"name": "base-cuda.json", "type": "file"},
                {"name": "subdir", "type": "dir"},
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = list_image_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_ok()
    assert result.unwrap() == ["base-cuda", "base-ubuntu"]


def test_list_image_names_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = list_image_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)


def test_fetch_image_metadata_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "base-ubuntu",
                "description": "Ubuntu base.",
                "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "aliases": ["ubuntu"],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_image_metadata(
        client, "jesserobertson/devcontainers", "main", "base-ubuntu"
    )
    assert result.is_ok()
    assert result.unwrap()["ref"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_fetch_image_metadata_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_image_metadata(
        client, "jesserobertson/devcontainers", "main", "nonexistent"
    )
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_github.py -v -k image`
Expected: FAIL with `ImportError: cannot import name 'list_image_names'`

- [ ] **Step 3: Write the implementation**

In `dvt/src/devtemplate/github.py`, update `__all__` and append:

```python
__all__ = ["fetch_image_metadata", "fetch_template", "list_image_names", "list_template_names"]


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def list_image_names(
    client: httpx.Client, repo: str, branch: str
) -> Result[list[str], Exception]:
    def _fetch() -> list[str]:
        url = f"https://api.github.com/repos/{repo}/contents/images?ref={branch}"
        response = client.get(url)
        response.raise_for_status()
        entries = response.json()
        return sorted(
            entry["name"].removesuffix(".json")
            for entry in entries
            if entry["type"] == "file" and entry["name"].endswith(".json")
        )

    return execute(_fetch)


@on_err(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    log_attempts=True,
)
def fetch_image_metadata(
    client: httpx.Client, repo: str, branch: str, name: str
) -> Result[dict[str, Any], Exception]:
    def _fetch() -> dict[str, Any]:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/images/{name}.json"
        response = client.get(url)
        response.raise_for_status()
        return cast(dict[str, Any], json.loads(response.text))

    return execute(_fetch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_github.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/github.py dvt/tests/test_github.py
git commit -m "feat(dvt): add GitHub fetch functions for the image registry"
```

---

### Task 5: `images.py` — read path (manifest, sync, list, load)

**Files:**
- Create: `dvt/src/devtemplate/images.py`
- Test: `dvt/tests/test_images.py`

**Interfaces:**
- Consumes: `list_image_names`, `fetch_image_metadata` (Task 4); `Settings.images_dir`/`image_manifest_path` (Task 3).
- Produces: `validate_image_name(name) -> Result[str, Exception]`; `read_image_manifest(settings) -> Result[list[str], Exception]`; `write_image_manifest(settings, managed_images) -> Result[None, Exception]`; `sync_images(settings, client) -> Result[list[str], Exception]`; `list_cached_images(settings) -> list[str]`; `load_cached_image(settings, name) -> Result[dict[str, Any], Exception]`.

This task mirrors `store.py`'s template functions almost line-for-line, with `images_dir / f"{name}.json"` in place of `templates_dir / name / "devcontainer.json"` (images are one file each, not a directory per image).

- [ ] **Step 1: Write the failing tests**

```python
# dvt/tests/test_images.py
import json

import httpx
import pytest

from devtemplate.images import (
    list_cached_images,
    load_cached_image,
    read_image_manifest,
    sync_images,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_writes_images_and_manifest(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(
            200,
            json={"name": "base-ubuntu", "ref": "ghcr.io/x/base-ubuntu:latest"},
        )

    result = sync_images(settings, _client(handler))

    assert result.is_ok()
    assert result.unwrap() == ["base-ubuntu"]
    assert list_cached_images(settings) == ["base-ubuntu"]

    loaded = load_cached_image(settings, "base-ubuntu")
    assert loaded.is_ok()
    assert loaded.unwrap() == {"name": "base-ubuntu", "ref": "ghcr.io/x/base-ubuntu:latest"}

    manifest = read_image_manifest(settings)
    assert manifest.is_ok()
    assert manifest.unwrap() == ["base-ubuntu"]


def test_sync_does_not_touch_custom_image_files(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "my-custom.json").write_text('{"name": "my-custom"}')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(200, json={"name": "base-ubuntu"})

    result = sync_images(settings, _client(handler))

    assert result.is_ok()
    assert (settings.images_dir / "my-custom.json").read_text() == '{"name": "my-custom"}'
    assert "my-custom" not in read_image_manifest(settings).unwrap()


def test_load_cached_image_missing_returns_err(settings):
    result = load_cached_image(settings, "nonexistent")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_list_cached_images_empty_before_sync(settings):
    assert list_cached_images(settings) == []


@pytest.mark.parametrize(
    "name", ["..", "has space", "UPPERCASE", "-leading-dash", "has_underscore", ""]
)
def test_load_cached_image_rejects_invalid_name(settings, name):
    result = load_cached_image(settings, name)
    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)


def test_sync_prunes_images_removed_upstream(settings):
    def handler_v1(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(
                200,
                json=[
                    {"name": "base-ubuntu.json", "type": "file"},
                    {"name": "old-image.json", "type": "file"},
                ],
            )
        if request.url.path.endswith("old-image.json"):
            return httpx.Response(200, json={"name": "old-image"})
        return httpx.Response(200, json={"name": "base-ubuntu"})

    first = sync_images(settings, _client(handler_v1))
    assert first.is_ok()
    assert set(list_cached_images(settings)) == {"base-ubuntu", "old-image"}

    def handler_v2(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(200, json={"name": "base-ubuntu"})

    second = sync_images(settings, _client(handler_v2))

    assert second.is_ok()
    assert list_cached_images(settings) == ["base-ubuntu"]


def test_sync_rejects_malicious_image_name_from_github(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "...json", "type": "file"}])
        return httpx.Response(200, json={"name": "escape"})

    result = sync_images(settings, _client(handler))

    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)
    assert list_cached_images(settings) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_images.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.images'`

- [ ] **Step 3: Write the implementation**

```python
# dvt/src/devtemplate/images.py
from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx
from logerr import Result
from logerr.itertools import traverse_result
from logerr.utilities import wrap_result

from devtemplate.config import Settings
from devtemplate.github import fetch_image_metadata, list_image_names

IMAGE_MANIFEST_KEY = "managed_images"
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

__all__ = [
    "list_cached_images",
    "load_cached_image",
    "read_image_manifest",
    "sync_images",
    "validate_image_name",
    "write_image_manifest",
]


def validate_image_name(name: str) -> Result[str, Exception]:
    error: Exception = ValueError(
        f"Invalid image name {name!r}: must match {IMAGE_NAME_PATTERN.pattern!r}"
    )
    return Result.from_predicate(
        name, lambda n: bool(IMAGE_NAME_PATTERN.fullmatch(n)), error
    )


@wrap_result
def read_image_manifest(settings: Settings) -> list[str]:
    if not settings.image_manifest_path.exists():
        return []
    data: dict[str, Any] = json.loads(settings.image_manifest_path.read_text())
    return cast(list[str], data.get(IMAGE_MANIFEST_KEY, []))


@wrap_result
def write_image_manifest(settings: Settings, managed_images: list[str]) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.image_manifest_path.write_text(
        json.dumps({IMAGE_MANIFEST_KEY: sorted(managed_images)}, indent=2)
    )


@wrap_result
def sync_images(settings: Settings, client: httpx.Client) -> list[str]:
    """Fetch every images/<name>.json listed on GitHub into the local cache.

    Same contract as devtemplate.store.sync_templates: only ever writes to
    names GitHub currently lists (all validated first), prunes any name
    that was in the *previous* sync's manifest but is missing from this
    one, and never touches a file that was never in any manifest dvt
    itself wrote.
    """
    names = list_image_names(
        client, settings.github_repo, settings.github_branch
    ).unwrap()

    traverse_result(names, validate_image_name).unwrap()

    previous_names = read_image_manifest(settings).unwrap_or([])

    settings.images_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        metadata = fetch_image_metadata(
            client, settings.github_repo, settings.github_branch, name
        ).unwrap()
        (settings.images_dir / f"{name}.json").write_text(json.dumps(metadata, indent=2))

    removed = set(previous_names) - set(names)
    for stale_name in removed:
        if validate_image_name(stale_name).is_err():
            continue
        stale_file = settings.images_dir / f"{stale_name}.json"
        if stale_file.is_file():
            stale_file.unlink()

    write_image_manifest(settings, names).unwrap()
    return names


def list_cached_images(settings: Settings) -> list[str]:
    if not settings.images_dir.exists():
        return []
    return sorted(p.stem for p in settings.images_dir.glob("*.json"))


@wrap_result
def load_cached_image(settings: Settings, name: str) -> dict[str, Any]:
    validate_image_name(name).unwrap()
    path = settings.images_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No cached image named {name!r}. Run 'dvt image sync' first."
        )
    return cast(dict[str, Any], json.loads(path.read_text()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_images.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/images.py dvt/tests/test_images.py
git commit -m "feat(dvt): add image registry read path (sync/list/load)"
```

---

### Task 6: `images.py` — write path (repo-root discovery, create/update/delete)

**Files:**
- Modify: `dvt/src/devtemplate/images.py`
- Test: `dvt/tests/test_images.py`

**Interfaces:**
- Produces: `find_repo_root(start: Path) -> Result[Path, Exception]`; `create_image_file(repo_root: Path, name: str, *, ref: str, description: str, aliases: list[str]) -> Result[Path, Exception]`; `update_image_file(repo_root: Path, name: str, *, ref: str | None = None, description: str | None = None, aliases: list[str] | None = None) -> Result[Path, Exception]`; `delete_image_file(repo_root: Path, name: str) -> Result[Path, Exception]`.

All three write functions operate on `<repo_root>/images/<name>.json` directly — no GitHub API call, no git command. `find_repo_root` walks upward from `start` for a `.git` directory.

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_images.py` (`json` is already imported at the top of this file, from Step 1's `test_sync_writes_images_and_manifest`, etc. — no new import needed here):

```python
from devtemplate.images import (
    create_image_file,
    delete_image_file,
    find_repo_root,
    update_image_file,
)


def test_find_repo_root_finds_git_dir_in_an_ancestor(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "dvt" / "src"
    nested.mkdir(parents=True)

    result = find_repo_root(nested)

    assert result.is_ok()
    assert result.unwrap() == tmp_path


def test_find_repo_root_returns_err_when_no_git_dir_found(tmp_path):
    lonely = tmp_path / "no-git-anywhere-near-here"
    lonely.mkdir()

    result = find_repo_root(lonely)

    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_create_image_file_writes_expected_json(tmp_path):
    (tmp_path / ".git").mkdir()

    result = create_image_file(
        tmp_path,
        "base-ubuntu",
        ref="ghcr.io/jesserobertson/base-ubuntu:latest",
        description="Ubuntu base.",
        aliases=["ubuntu", "default"],
    )

    assert result.is_ok()
    written = json.loads((tmp_path / "images" / "base-ubuntu.json").read_text())
    assert written == {
        "name": "base-ubuntu",
        "description": "Ubuntu base.",
        "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "aliases": ["ubuntu", "default"],
    }


def test_create_image_file_refuses_when_already_exists(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = create_image_file(
        tmp_path, "base-ubuntu", ref="x", description="y", aliases=[]
    )

    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileExistsError)


def test_update_image_file_edits_only_given_fields(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text(
        json.dumps(
            {
                "name": "base-ubuntu",
                "description": "old description",
                "ref": "ghcr.io/x/base-ubuntu:latest",
                "aliases": ["ubuntu"],
            }
        )
    )

    result = update_image_file(tmp_path, "base-ubuntu", description="new description")

    assert result.is_ok()
    written = json.loads((images_dir / "base-ubuntu.json").read_text())
    assert written["description"] == "new description"
    assert written["ref"] == "ghcr.io/x/base-ubuntu:latest"
    assert written["aliases"] == ["ubuntu"]


def test_update_image_file_refuses_when_missing(tmp_path):
    result = update_image_file(tmp_path, "nonexistent", description="x")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_delete_image_file_removes_the_file(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = delete_image_file(tmp_path, "base-ubuntu")

    assert result.is_ok()
    assert not (images_dir / "base-ubuntu.json").exists()


def test_delete_image_file_refuses_when_missing(tmp_path):
    result = delete_image_file(tmp_path, "nonexistent")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_images.py -v -k "repo_root or image_file"`
Expected: FAIL with `ImportError: cannot import name 'find_repo_root'`

- [ ] **Step 3: Write the implementation**

Append to `dvt/src/devtemplate/images.py` (add `Path` to the existing `from pathlib import Path` import if not already present, and extend `__all__`):

```python
# add near the top imports:
from pathlib import Path

# extend __all__:
__all__ = [
    "create_image_file",
    "delete_image_file",
    "find_repo_root",
    "list_cached_images",
    "load_cached_image",
    "read_image_manifest",
    "sync_images",
    "update_image_file",
    "validate_image_name",
    "write_image_manifest",
]


@wrap_result
def find_repo_root(start: Path) -> Path:
    """Walk upward from `start` (inclusive) looking for a `.git` directory.

    dvt image create/update/delete edit images/*.json directly in a real
    checkout of the source repo (see module docstring / design spec) -
    unlike sync/list/show, which work from a plain XDG cache with no
    checkout required at all.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError(
        f"No .git directory found in {start} or any parent - 'dvt image "
        "create/update/delete' must be run from inside a checkout of the "
        "devcontainers repo."
    )


@wrap_result
def create_image_file(
    repo_root: Path, name: str, *, ref: str, description: str, aliases: list[str]
) -> Path:
    validate_image_name(name).unwrap()
    images_dir = repo_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    path = images_dir / f"{name}.json"
    if path.exists():
        raise FileExistsError(
            f"{path} already exists. Use 'dvt image update {name}' to change it."
        )
    metadata = {"name": name, "description": description, "ref": ref, "aliases": aliases}
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return path


@wrap_result
def update_image_file(
    repo_root: Path,
    name: str,
    *,
    ref: str | None = None,
    description: str | None = None,
    aliases: list[str] | None = None,
) -> Path:
    validate_image_name(name).unwrap()
    path = repo_root / "images" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist. Use 'dvt image create {name}' first."
        )
    metadata = cast(dict[str, Any], json.loads(path.read_text()))
    if ref is not None:
        metadata["ref"] = ref
    if description is not None:
        metadata["description"] = description
    if aliases is not None:
        metadata["aliases"] = aliases
    path.write_text(json.dumps(metadata, indent=2) + "\n")
    return path


@wrap_result
def delete_image_file(repo_root: Path, name: str) -> Path:
    validate_image_name(name).unwrap()
    path = repo_root / "images" / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    path.unlink()
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_images.py -v`
Expected: PASS (15 tests total)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/images.py dvt/tests/test_images.py
git commit -m "feat(dvt): add local-file CRUD for the image registry"
```

---

### Task 7: `images.py` — `resolve_image_ref`

**Files:**
- Modify: `dvt/src/devtemplate/images.py`
- Test: `dvt/tests/test_images.py`

**Interfaces:**
- Consumes: `resolve_or_confirm` (Task 1); `list_cached_images`, `load_cached_image` (Task 5).
- Produces: `resolve_image_ref(query: str, settings: Settings, *, assume_yes: bool = False, interactive: bool = True) -> Result[str, Exception]`.

Resolution order: empty cache → passthrough; exact match against any cached image's `ref` → passthrough; exact match against a `name` or `alias` → that image's `ref`, no prompt; no difflib-close candidate at all → passthrough (treat as a literal, dvt-unknown ref); otherwise delegate the confirm/`--yes`/non-interactive behavior to `resolve_or_confirm` and map its `Ok` back through the name→ref lookup, propagating its `Err` (declined confirmation, or non-interactive with a suggestion) rather than swallowing it.

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_images.py`:

```python
from devtemplate.images import resolve_image_ref


def _write_image(settings, name, ref, aliases=None):
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    (settings.images_dir / f"{name}.json").write_text(
        json.dumps(
            {"name": name, "description": "", "ref": ref, "aliases": aliases or []}
        )
    )


def test_resolve_image_ref_passthrough_on_empty_cache(settings):
    result = resolve_image_ref("anything", settings)
    assert result.is_ok()
    assert result.unwrap() == "anything"


def test_resolve_image_ref_exact_ref_passes_through(settings):
    _write_image(settings, "base-ubuntu", "ghcr.io/x/base-ubuntu:latest")
    result = resolve_image_ref("ghcr.io/x/base-ubuntu:latest", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-ubuntu:latest"


def test_resolve_image_ref_exact_name_resolves_to_ref(settings):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    result = resolve_image_ref("base-cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_exact_alias_resolves_to_ref(settings):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest", aliases=["cuda", "gpu"])
    result = resolve_image_ref("cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_no_close_match_passes_through_as_literal(settings):
    _write_image(settings, "base-ubuntu", "ghcr.io/x/base-ubuntu:latest")
    result = resolve_image_ref("myregistry.example.com/custom:latest", settings)
    assert result.is_ok()
    assert result.unwrap() == "myregistry.example.com/custom:latest"


def test_resolve_image_ref_close_typo_confirmed_yes_resolves(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = resolve_image_ref("bas-cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_close_typo_confirmed_no_returns_err(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    result = resolve_image_ref("bas-cuda", settings)
    assert result.is_err()


def test_resolve_image_ref_assume_yes_skips_prompt(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    result = resolve_image_ref("bas-cuda", settings, assume_yes=True)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_non_interactive_close_typo_fails_with_suggestion(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    result = resolve_image_ref("bas-cuda", settings, interactive=False)
    assert result.is_err()
    assert "base-cuda" in str(result.unwrap_err())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_images.py -v -k resolve_image_ref`
Expected: FAIL with `ImportError: cannot import name 'resolve_image_ref'`

- [ ] **Step 3: Write the implementation**

Append to `dvt/src/devtemplate/images.py` (add `difflib` and `Err`/`Ok` to the existing imports, extend `__all__`):

```python
# add to imports:
import difflib

from logerr import Err, Ok, Result  # replaces the earlier `from logerr import Result`

from devtemplate.fuzzy import resolve_or_confirm

# extend __all__ with "resolve_image_ref"


def resolve_image_ref(
    query: str,
    settings: Settings,
    *,
    assume_yes: bool = False,
    interactive: bool = True,
) -> Result[str, Exception]:
    """Resolve an --image argument to a full OCI ref.

    Exact match against a cached image's own ref, or its name/alias,
    resolves with no prompt. A close (but not exact) match to a name/alias
    goes through resolve_or_confirm - propagating a declined confirmation
    or a non-interactive suggestion as a real Err, since the user (or the
    fuzzy match itself) has something specific to say about it. An empty
    cache, or a query with no close match at all, passes the query through
    unchanged - this only ever helps resolve a name/alias dvt already
    knows about, it never blocks a literal ref that used to work.
    """
    names = list_cached_images(settings)
    if not names:
        return Ok(query)

    images: list[dict[str, Any]] = []
    for name in names:
        match load_cached_image(settings, name):
            case Ok(metadata):
                images.append(metadata)
            case Err(_):
                continue

    if any(query == image.get("ref") for image in images):
        return Ok(query)

    lookup: dict[str, str] = {}
    for image in images:
        lookup[image["name"]] = image["ref"]
        for alias in image.get("aliases", []):
            lookup[alias] = image["ref"]

    if query in lookup:
        return Ok(lookup[query])

    if not difflib.get_close_matches(query, sorted(lookup), n=1, cutoff=0.6):
        return Ok(query)

    resolved = resolve_or_confirm(
        query, sorted(lookup), label="image", assume_yes=assume_yes, interactive=interactive
    )
    match resolved:
        case Ok(matched_key):
            return Ok(lookup[matched_key])
        case Err(error):
            return Err(error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_images.py -v`
Expected: PASS (24 tests total)

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/images.py dvt/tests/test_images.py
git commit -m "feat(dvt): add resolve_image_ref for name/alias/ref resolution"
```

---

### Task 8: `commands/image.py` — sync/list/show + output schemas

**Files:**
- Create: `dvt/src/devtemplate/commands/image.py`
- Modify: `dvt/src/devtemplate/cli_output_schemas.py`
- Test: `dvt/tests/test_image_command.py`

**Interfaces:**
- Consumes: `sync_images`, `list_cached_images`, `load_cached_image` (Task 5); `fuzzy_argument` (Task 2); `unwrap_or_exit`, `emit_success`, `with_status` from `cli_support`.
- Produces: `devtemplate.commands.image.app` (a `typer.Typer` with `sync`/`list`/`show` commands so far — `create`/`update`/`delete` land in Task 9); `ImageListOutput`, `ImageShowOutput`, `ImageSyncOutput` in `cli_output_schemas.py`.

This mirrors `commands/feature.py`'s `list_features`/`show_feature`/`sync` almost exactly.

- [ ] **Step 1: Write the failing tests**

```python
# dvt/tests/test_image_command.py
from __future__ import annotations

import json

import jsonschema
from typer.testing import CliRunner

from devtemplate import __version__
from devtemplate.cli import app as real_app
from devtemplate.cli_support import describe_app
from devtemplate.commands.image import app

runner = CliRunner()


def _assert_matches_declared_output_schema(command_name: str, payload: dict) -> None:
    schema = describe_app(real_app, version=__version__)["commands"][command_name][
        "output"
    ]["success"]
    jsonschema.validate(instance=payload, schema=schema)


def test_list_reports_no_images_when_cache_empty(settings):
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "No cached images" in result.stdout


def test_list_shows_cached_image_name_and_ref(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps(
            {
                "name": "base-cuda",
                "description": "CUDA base.",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        )
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout
    assert "ghcr.io/jesserobertson/base-cuda:latest" in result.stdout


def test_list_json_output_includes_all_fields(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps(
            {
                "name": "base-cuda",
                "description": "CUDA base.",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        )
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "name": "base-cuda",
            "description": "CUDA base.",
            "ref": "ghcr.io/jesserobertson/base-cuda:latest",
        }
    ]
    _assert_matches_declared_output_schema("image list", rows)


def test_show_prints_cached_image(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )

    result = runner.invoke(app, ["show", "base-cuda"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout


def test_show_json_prints_the_raw_cached_image_on_success(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )

    result = runner.invoke(app, ["show", "base-cuda", "--json"])
    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"}
    _assert_matches_declared_output_schema("image show", printed)


def test_show_fuzzy_resolves_a_close_typo(settings, monkeypatch):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-cuda.json").write_text(
        json.dumps({"name": "base-cuda", "ref": "ghcr.io/x/base-cuda:latest"})
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["show", "bas-cuda"])
    assert result.exit_code == 0, result.output
    assert "base-cuda" in result.stdout


def test_sync_reports_synced_image_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Ok(["base-cuda", "base-ubuntu"]),
    )

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "base-cuda" in result.stdout
    assert "base-ubuntu" in result.stdout


def test_sync_json_prints_ok_true_with_synced_names(settings, monkeypatch):
    from logerr import Ok

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Ok(["base-cuda"]),
    )

    result = runner.invoke(app, ["sync", "--json"])
    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert printed == {"ok": True, "synced": ["base-cuda"]}
    _assert_matches_declared_output_schema("image sync", printed)


def test_sync_json_prints_ok_false_on_failure(settings, monkeypatch):
    from logerr import Err

    monkeypatch.setattr(
        "devtemplate.commands.image.sync_images",
        lambda settings_arg, client: Err(RuntimeError("network unreachable")),
    )

    result = runner.invoke(app, ["sync", "--json"])
    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "network unreachable" in printed["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_image_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'devtemplate.commands.image'`

- [ ] **Step 3a: Add the output schema models**

In `dvt/src/devtemplate/cli_output_schemas.py`, add to `__all__` and add the three model classes (near the existing `Feature*Output` classes):

```python
# add to __all__:
    "ImageListOutput",
    "ImageShowOutput",
    "ImageSyncOutput",


class ImageListOutput(RootModel[list[dict[str, Any]]]):
    """No {"ok": ...} envelope, matching FeatureListOutput's convention."""


class ImageShowOutput(RootModel[dict[str, Any]]):
    """Raw pass-through of the cached image's own metadata JSON."""


class ImageSyncOutput(BaseModel):
    ok: Literal[True]
    synced: list[str]
```

And register them in `OUTPUT_MODELS`:

```python
    "image list": ImageListOutput,
    "image show": ImageShowOutput,
    "image sync": ImageSyncOutput,
```

- [ ] **Step 3b: Write `commands/image.py`**

```python
# dvt/src/devtemplate/commands/image.py
from __future__ import annotations

import json
from typing import Any

import httpx
import typer
from logerr import Err, Ok, Result
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status
from devtemplate.config import load_settings
from devtemplate.fuzzy import fuzzy_argument
from devtemplate.images import list_cached_images, load_cached_image, sync_images

__all__ = ["app"]

app = typer.Typer(help="List, sync, and manage the base images dvt knows about.")
console = Console()
stderr_console = Console(stderr=True)


@app.command("sync")
def sync(
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Refresh the cached image registry from GitHub."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    def do_sync(_status: object) -> Result[list[str], Exception]:
        with httpx.Client() as client:
            return sync_images(settings, client)

    result = with_status(json_output, console, "Syncing images from GitHub...", do_sync)
    names = unwrap_or_exit(
        result, console, prefix="Sync failed: ", json_output=json_output
    )
    emit_success(
        json_output,
        {"synced": names},
        lambda: console.print(f"Synced {len(names)} images: {', '.join(names)}"),
    )


@app.command("list")
def list_images(
    json_output: bool = typer.Option(  # noqa: B008
        False, "--json", help="Print machine-readable JSON instead of a table."
    ),
) -> None:
    """List every image dvt knows about, with its description and ref."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    names = list_cached_images(settings)
    if not names and not json_output:
        console.print("No cached images. Run 'dvt image sync' first.")
        raise typer.Exit(code=0)

    rows: list[dict[str, str]] = []
    for name in names:
        match load_cached_image(settings, name):
            case Ok(image):
                rows.append(
                    {
                        "name": name,
                        "description": image.get("description", ""),
                        "ref": image.get("ref", ""),
                    }
                )
            case Err(error):
                stderr_console.print(
                    f"[red]Skipping {escape(repr(name))}: {escape(str(error))}[/red]"
                )

    if json_output:
        print(json.dumps(rows))
        return

    table = Table("Name", "Description", "Ref")
    for row in rows:
        table.add_row(row["name"], row["description"], row["ref"])
    console.print(table)


@app.command("show")
@fuzzy_argument("name", candidates_fn=list_cached_images, label="image", console=console)
def show_image(
    name: str = typer.Argument(..., help="Cached image name to show."),  # noqa: B008
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text on failure.",
    ),
) -> None:
    """Print a cached image's raw metadata."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    image = unwrap_or_exit(
        load_cached_image(settings, name), console, json_output=json_output
    )
    print(json.dumps(image, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_image_command.py -v`
Expected: PASS (9 tests) — note `describe_app`/`_assert_matches_declared_output_schema` calls read from `devtemplate.cli.app`, which won't have `image` registered until Task 10; if any schema-lookup test fails with a `KeyError` at this point, that's expected — re-run this file's tests again after Task 10 wires `image_app` into `cli.py` to confirm they pass end to end.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/image.py dvt/src/devtemplate/cli_output_schemas.py dvt/tests/test_image_command.py
git commit -m "feat(dvt): add dvt image sync/list/show commands"
```

---

### Task 9: `commands/image.py` — create/update/delete + output schemas

**Files:**
- Modify: `dvt/src/devtemplate/commands/image.py`
- Modify: `dvt/src/devtemplate/cli_output_schemas.py`
- Test: `dvt/tests/test_image_command.py`

**Interfaces:**
- Consumes: `find_repo_root`, `create_image_file`, `update_image_file`, `delete_image_file` (Task 6); `fuzzy_argument` (Task 2, for `update`/`delete` only — `create` takes a brand-new name, no candidates to resolve against).
- Produces: `ImageCreateOutput`, `ImageUpdateOutput`, `ImageDeleteOutput` in `cli_output_schemas.py`; `create`/`update`/`delete` commands on `devtemplate.commands.image.app`.

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_image_command.py`:

```python
def test_create_writes_images_json_in_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    result = runner.invoke(
        app,
        [
            "create",
            "base-ubuntu",
            "--ref",
            "ghcr.io/jesserobertson/base-ubuntu:latest",
            "--description",
            "Ubuntu base.",
            "--alias",
            "ubuntu",
        ],
    )

    assert result.exit_code == 0, result.output
    written = json.loads((tmp_path / "images" / "base-ubuntu.json").read_text())
    assert written == {
        "name": "base-ubuntu",
        "description": "Ubuntu base.",
        "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "aliases": ["ubuntu"],
    }


def test_create_json_prints_ok_true_with_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    result = runner.invoke(
        app,
        ["create", "base-ubuntu", "--ref", "x", "--description", "y", "--json"],
    )

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["ok"] is True
    assert printed["name"] == "base-ubuntu"
    _assert_matches_declared_output_schema("image create", printed)


def test_create_fails_outside_a_git_checkout(tmp_path, monkeypatch):
    lonely = tmp_path / "no-git-here"
    lonely.mkdir()
    monkeypatch.chdir(lonely)

    result = runner.invoke(
        app, ["create", "base-ubuntu", "--ref", "x", "--description", "y"]
    )

    assert result.exit_code == 1


def test_update_edits_the_existing_repo_local_file(tmp_path, monkeypatch, settings):
    # repo_dir is a SUBdirectory of tmp_path, not tmp_path itself: the settings
    # fixture points settings.images_dir at tmp_path/"images" (data_dir == tmp_path),
    # so a repo root directly at tmp_path would make repo_dir/"images" collide with
    # settings.images_dir on disk - update's fuzzy-resolved <name> argument needs
    # both directories to exist independently (the XDG cache for name resolution,
    # the repo checkout for the actual file being edited).
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text(
        json.dumps(
            {"name": "base-ubuntu", "description": "old", "ref": "x", "aliases": []}
        )
    )
    # update's `<name>` argument is fuzzy-resolved against the cached image
    # registry (Task 2's decorator), so the local XDG cache needs the name too.
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text(
        json.dumps({"name": "base-ubuntu"})
    )

    result = runner.invoke(app, ["update", "base-ubuntu", "--description", "new"])

    assert result.exit_code == 0, result.output
    written = json.loads((repo_dir / "images" / "base-ubuntu.json").read_text())
    assert written["description"] == "new"


def test_delete_removes_the_repo_local_file(tmp_path, monkeypatch, settings):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = runner.invoke(app, ["delete", "base-ubuntu"])

    assert result.exit_code == 0, result.output
    assert not (repo_dir / "images" / "base-ubuntu.json").exists()


def test_delete_json_prints_ok_true_with_path(tmp_path, monkeypatch, settings):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    (repo_dir / ".git").mkdir()
    (repo_dir / "images").mkdir()
    (repo_dir / "images" / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = runner.invoke(app, ["delete", "base-ubuntu", "--json"])

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["ok"] is True
    _assert_matches_declared_output_schema("image delete", printed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_image_command.py -v -k "create or update or delete"`
Expected: FAIL — `create`/`update`/`delete` aren't registered commands yet (Click reports "No such command").

- [ ] **Step 3a: Add the output schema models**

In `dvt/src/devtemplate/cli_output_schemas.py`:

```python
# add to __all__:
    "ImageCreateOutput",
    "ImageDeleteOutput",
    "ImageUpdateOutput",


class ImageCreateOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class ImageUpdateOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str


class ImageDeleteOutput(BaseModel):
    ok: Literal[True]
    name: str
    path: str
```

And register in `OUTPUT_MODELS`:

```python
    "image create": ImageCreateOutput,
    "image update": ImageUpdateOutput,
    "image delete": ImageDeleteOutput,
```

- [ ] **Step 3b: Append the commands to `commands/image.py`**

Add `from pathlib import Path` to the imports, add `create_image_file, delete_image_file, find_repo_root, update_image_file` to the `devtemplate.images` import, and append:

```python
@app.command("create")
def create(
    name: str = typer.Argument(..., help="New image name."),  # noqa: B008
    ref: str = typer.Option(  # noqa: B008
        ..., "--ref", help="Full OCI ref, e.g. ghcr.io/jesserobertson/base-ubuntu:latest."
    ),
    description: str = typer.Option(  # noqa: B008
        ..., "--description", help="Short human-readable description."
    ),
    alias: list[str] = typer.Option(  # noqa: B008
        [],
        "--alias",
        help="Alternate name(s) this image can be resolved by (repeatable).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Write images/<name>.json in the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    path = unwrap_or_exit(
        create_image_file(repo_root, name, ref=ref, description=description, aliases=alias),
        console,
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": name, "path": str(path)},
        lambda: console.print(
            f"Wrote {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )


@app.command("update")
@fuzzy_argument("name", candidates_fn=list_cached_images, label="image", console=console)
def update(
    name: str = typer.Argument(..., help="Cached image name to update."),  # noqa: B008
    ref: str | None = typer.Option(None, "--ref", help="New OCI ref."),  # noqa: B008
    description: str | None = typer.Option(  # noqa: B008
        None, "--description", help="New description."
    ),
    alias: list[str] | None = typer.Option(  # noqa: B008
        None,
        "--alias",
        help="New alias list (repeatable; replaces the existing list entirely).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Edit fields on images/<name>.json in the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    path = unwrap_or_exit(
        update_image_file(repo_root, name, ref=ref, description=description, aliases=alias),
        console,
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": name, "path": str(path)},
        lambda: console.print(
            f"Updated {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )


@app.command("delete")
@fuzzy_argument("name", candidates_fn=list_cached_images, label="image", console=console)
def delete(
    name: str = typer.Argument(..., help="Cached image name to delete."),  # noqa: B008
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Remove images/<name>.json from the current repo checkout.

    Doesn't publish to GitHub - commit and push (or open a PR) yourself.
    """
    repo_root = unwrap_or_exit(
        find_repo_root(Path.cwd()), console, json_output=json_output
    )
    path = unwrap_or_exit(
        delete_image_file(repo_root, name), console, json_output=json_output
    )
    emit_success(
        json_output,
        {"name": name, "path": str(path)},
        lambda: console.print(
            f"Removed {escape(str(path))}. This only changed your local checkout - "
            "commit and push (or open a PR) to publish it."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_image_command.py -v`
Expected: PASS (15 tests) — same schema-lookup caveat as Task 8's Step 4 applies until Task 10.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/image.py dvt/src/devtemplate/cli_output_schemas.py dvt/tests/test_image_command.py
git commit -m "feat(dvt): add dvt image create/update/delete commands"
```

---

### Task 10: Wire `dvt image` into the top-level CLI

**Files:**
- Modify: `dvt/src/devtemplate/commands/__init__.py`
- Modify: `dvt/src/devtemplate/cli.py:27,37` (imports and `app.add_typer` calls)
- Test: `dvt/tests/test_cli_help.py`, `dvt/tests/test_image_command.py` (re-run only)

**Interfaces:**
- Consumes: `devtemplate.commands.image.app` (Tasks 8-9).
- Produces: `dvt image ...` reachable from the real top-level `dvt` CLI, so `describe_app`/`--describe` and every `_assert_matches_declared_output_schema` call in `test_image_command.py` resolve correctly.

- [ ] **Step 1: Write the failing test**

Add to `dvt/tests/test_cli_help.py`:

```python
def test_image_is_registered_as_a_top_level_command_group():
    names = set(root.commands.keys())
    assert "image" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run pytest tests/test_cli_help.py -v -k image`
Expected: FAIL — `"image" in names` is `False`.

- [ ] **Step 3: Wire it up**

`dvt/src/devtemplate/commands/__init__.py`, full replacement:

```python
from devtemplate.commands.feature import app as feature_app
from devtemplate.commands.image import app as image_app
from devtemplate.commands.info import info as info_command
from devtemplate.commands.init import init as init_command

__all__ = ["feature_app", "image_app", "info_command", "init_command"]
```

`dvt/src/devtemplate/cli.py` line 27, change:

```python
from devtemplate.commands import feature_app, info_command, init_command
```

to:

```python
from devtemplate.commands import feature_app, image_app, info_command, init_command
```

and line 37, change:

```python
app.add_typer(feature_app, name="feature")
```

to:

```python
app.add_typer(feature_app, name="feature")
app.add_typer(image_app, name="image")
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `dvt/`): `pixi run pytest tests/test_cli_help.py tests/test_image_command.py tests/test_cli_describe.py -v`
Expected: PASS — including every `_assert_matches_declared_output_schema` call in `test_image_command.py`, now that `image` is reachable from `devtemplate.cli.app`.

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/__init__.py dvt/src/devtemplate/cli.py dvt/tests/test_cli_help.py
git commit -m "feat(dvt): register dvt image as a top-level command group"
```

---

### Task 11: Fuzzy-match `dvt feature add/remove/show`

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py:1-100` (imports plus the `add`, `remove`, `show_feature` command definitions)
- Test: `dvt/tests/test_feature_command.py`

**Interfaces:**
- Consumes: `fuzzy_argument` (Task 2); `list_cached_templates` (already imported in `feature.py`).

- [ ] **Step 1: Write the failing tests**

Append to `dvt/tests/test_feature_command.py`:

```python
def test_show_fuzzy_resolves_a_close_typo(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["show", "fastpi"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.stdout


def test_show_yes_flag_skips_the_prompt(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )

    result = runner.invoke(app, ["show", "fastpi", "--yes"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.stdout


def test_show_json_mode_with_typo_fails_with_suggestion_no_hang(settings, monkeypatch):
    settings.templates_dir.mkdir(parents=True)
    (settings.templates_dir / "fastapi").mkdir()
    (settings.templates_dir / "fastapi" / "devcontainer.json").write_text(
        json.dumps({"name": "fastapi"})
    )
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )

    result = runner.invoke(app, ["show", "fastpi", "--json"])
    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "fastapi" in printed["error"]


def test_add_fuzzy_resolves_a_close_typo(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["add", "agnt"])

    assert result.exit_code == 0, result.output
    assert "Added feature 'agent'" in result.output


def test_remove_fuzzy_resolves_a_close_typo(tmp_path, settings, monkeypatch):
    monkeypatch.chdir(tmp_path)
    devcontainer_dir = tmp_path / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        json.dumps(
            {"name": "my-project", "image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
        )
    )
    template_dir = settings.templates_dir / "agent"
    template_dir.mkdir(parents=True)
    (template_dir / "devcontainer.json").write_text(json.dumps({"name": "agent"}))
    runner.invoke(app, ["add", "agent"])

    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = runner.invoke(app, ["remove", "agnt"])

    assert result.exit_code == 0, result.output
    assert "Removed feature 'agent'" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run pytest tests/test_feature_command.py -v -k "fuzzy or yes_flag or json_mode_with_typo"`
Expected: FAIL — a plain typo currently produces a flat "No cached feature named" error, not a prompt/resolution.

- [ ] **Step 3: Write the implementation**

**Design correction found during implementation (not the original plan text — read this before writing code):** the first draft of this task decorated `add`/`remove` with `fuzzy_argument` the same way as `show`. That breaks two existing, already-shipped guarantees: (a) `add`/`remove` apply their `names` list one at a time and — on a failure partway through — stop *but keep whatever was already applied before the failure*; `fuzzy_argument` resolves the *entire* list eagerly before the command body ever runs, so a bad name anywhere in the list now aborts before any name is applied, even ones listed earlier that would have succeeded. (b) `test_show_error_message_is_not_mangled_by_rich_markup` (a pre-existing test) breaks because fuzzy resolution now intercepts an invalid name *before* `load_cached_template`'s own regex-validation error (which happens to contain `[...]` characters the test was actually checking Rich-escaping against) ever runs.

The fix: `show` (single name, no partial-apply concern) keeps using `fuzzy_argument` exactly as before. `add`/`remove` (multi-name, apply-one-at-a-time-and-keep-earlier-successes) do NOT use the decorator — they call `resolve_or_confirm` directly, once per name, *inside* their existing per-name loop, immediately before that name is applied/removed. This preserves the exact original control flow and stop-on-first-failure-keep-earlier-successes contract, with fuzzy resolution added at the point each name is actually used.

In `dvt/src/devtemplate/commands/feature.py`, add the import:

```python
from devtemplate.fuzzy import fuzzy_argument, resolve_or_confirm
```

`show` — decorate as originally planned (add `@fuzzy_argument(...)` directly above `@app.command("show")`'s function, between the two decorators so it runs closer to the function):

```python
@app.command("show")
@fuzzy_argument("name", candidates_fn=list_cached_templates, label="feature", console=console)
def show_feature(
    name: str = typer.Argument(..., help="Cached feature name to show."),  # noqa: B008
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help='On failure, print {"ok": false, "error": ...} instead of '
        "human-readable text. Success output is unaffected - it's always the "
        "cached feature's raw devcontainer.json overlay.",
    ),
) -> None:
    ...  # body unchanged
```

`add` and `remove` — NO `fuzzy_argument` decorator. Add a `--yes`/`-y` option directly to each (mirroring the decorator's own flag name/help text, since they no longer get it injected automatically), and resolve each name via `resolve_or_confirm` inside the existing per-name loop:

```python
@app.command("add")
def add(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Cached feature name(s) to add, applied in order."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched feature name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Layer one or more features onto ./.devcontainer/devcontainer.json, in order."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    if not list_cached_templates(settings):

        def do_sync(_status: object) -> Result[list[str], Exception]:
            with httpx.Client() as client:
                return sync_templates(settings, client)

        sync_result = with_status(
            json_output, console, "Syncing features from GitHub...", do_sync
        )
        unwrap_or_exit(
            sync_result, console, prefix="Sync failed: ", json_output=json_output
        )

    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    resolved_names: list[str] = []
    for raw_name in names:
        resolved = unwrap_or_exit(
            resolve_or_confirm(
                raw_name,
                list_cached_templates(settings),
                label="feature",
                assume_yes=assume_yes,
                interactive=not json_output,
            ),
            console,
            json_output=json_output,
        )
        unwrap_or_exit(
            add_one(resolved, settings, devcontainer_dir, target, json_output=json_output),
            console,
            json_output=json_output,
        )
        resolved_names.append(resolved)
    emit_success(json_output, {"added": resolved_names}, lambda: None)
```

```python
@app.command("remove")
def remove(
    names: list[str] = typer.Argument(  # noqa: B008
        ..., help="Applied feature name(s) to remove, in order."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched feature name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Un-layer one or more features previously added with 'dvt feature add', in order."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    devcontainer_dir = Path(".devcontainer")
    target = devcontainer_dir / "devcontainer.json"
    resolved_names: list[str] = []
    for raw_name in names:
        resolved = unwrap_or_exit(
            resolve_or_confirm(
                raw_name,
                list_cached_templates(settings),
                label="feature",
                assume_yes=assume_yes,
                interactive=not json_output,
            ),
            console,
            json_output=json_output,
        )
        unwrap_or_exit(
            remove_one(resolved, devcontainer_dir, target, json_output=json_output),
            console,
            json_output=json_output,
        )
        resolved_names.append(resolved)
    emit_success(json_output, {"removed": resolved_names}, lambda: None)
```

Note this restores `add`'s original auto-sync block completely unchanged (verbatim, in its original position, with its original `with_status` spinner) — since `add`/`remove` no longer route through `fuzzy_argument`'s `candidates_fn` mechanism at all, there is no auto-sync/fuzzy-resolution ordering conflict to solve, and no `_template_names_with_auto_sync` helper is needed. `test_add_auto_syncs_when_cache_empty` and `test_add_shows_a_status_spinner_while_auto_syncing` (both pre-existing, unmodified) should pass completely unchanged — this design does not touch that behavior at all, unlike the first-draft approach.

`{"added": resolved_names}`/`{"removed": resolved_names}` report the *resolved* (canonical, post-typo-correction) names, not the raw argument values — more useful to a `--json` consumer than echoing back a typo that got corrected. This doesn't affect any existing test (none of them exercise fuzzy resolution together with `--json`), but is a deliberate, small design choice worth calling out.

**Fixing the pre-existing Rich-markup test.** `test_show_error_message_is_not_mangled_by_rich_markup` (already in `dvt/tests/test_feature_command.py`, predates this whole plan) invokes `runner.invoke(app, ["show", ".."])` and asserts the invalid-name regex pattern (`"[a-z0-9][a-z0-9-]"`, from `load_cached_template`'s own validation error) survives Rich-escaping in the output. With `show` now going through `fuzzy_argument` first, `".."` never reaches `load_cached_template` at all — `resolve_or_confirm` intercepts it first with its own error (`No feature named '..'. Known features: ...`), which contains no bracket characters, so the original assertion no longer has anything meaningful to check. Replace the test's body (keep the name) with an invocation that still exercises the same underlying concern — Rich markup embedded in *user-controlled* input must not corrupt the CLI's error output — through the new path:

```python
def test_show_error_message_is_not_mangled_by_rich_markup(settings, monkeypatch):
    # Rich's color_system is fixed at Console() construction time (module import),
    # from whatever FORCE_COLOR/TTY state was live then - so in an environment that
    # sets FORCE_COLOR, styled segments get ANSI codes even when writing to
    # CliRunner's non-tty buffer. Force no_color directly so this test checks the
    # actual rendered text, not ANSI-interleaved bytes.
    monkeypatch.setattr(console, "no_color", True)

    result = runner.invoke(app, ["show", "[red]hacked[/red]"])
    assert result.exit_code == 1
    assert "[red]hacked[/red]" in result.stdout
```

This directly tests the scenario `escape()` exists to guard against: a query the user typed containing literal Rich markup syntax (embedded via `resolve_or_confirm`'s `{query!r}` in its no-match error message) must render as inert plain text in the CLI's error output, not be interpreted as styling tags that hide or corrupt the message.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run pytest tests/test_feature_command.py -v`
Expected: PASS (every test in the file, including all pre-existing ones — `test_add_auto_syncs_when_cache_empty`, `test_add_shows_a_status_spinner_while_auto_syncing`, `test_add_stops_on_first_failure_leaving_earlier_successes_applied`, `test_remove_stops_on_first_failure_leaving_earlier_removals_applied` — completely unmodified and unaffected by this design, plus the corrected `test_show_error_message_is_not_mangled_by_rich_markup` above).

- [ ] **Step 5: Commit**

```bash
git add dvt/src/devtemplate/commands/feature.py dvt/tests/test_feature_command.py
git commit -m "feat(dvt): fuzzy-match dvt feature add/remove/show"
```

---

### Task 12: Wire `resolve_image_ref` into `dvt init --image`

**Files:**
- Modify: `dvt/src/devtemplate/commands/init.py`
- Modify: `dvt/tests/test_init.py`

**Interfaces:**
- Consumes: `resolve_image_ref` (Task 7); `load_settings` from `devtemplate.config`; `unwrap_or_exit` from `devtemplate.cli_support`.

- [ ] **Step 1: Update existing tests to use the `settings` fixture**

`init()` will call `load_settings()` internally after this task, which (via the `settings` fixture's `platformdirs.user_data_dir` monkeypatch) must resolve to an isolated `tmp_path`, not the real machine's XDG data dir. Add `settings` as a parameter to every existing test in `dvt/tests/test_init.py` that reaches past the "does `devcontainer.json` already exist" early-exit (the two tests that hit that early exit - `test_init_json_prints_ok_false_when_devcontainer_json_already_exists` and `test_init_refuses_to_overwrite_existing_devcontainer_json` - don't need it, since `init` will return before ever calling `load_settings()`; neither does `test_init_help_text_mentions_default_image`, since `--help` never invokes the command body at all):

```python
def test_init_scaffolds_devcontainer_json_with_defaults(tmp_path, settings):
    ...  # body unchanged

def test_init_image_option_overrides_default(tmp_path, settings):
    ...  # body unchanged

def test_init_json_prints_ok_true_with_path_on_success(tmp_path, settings):
    ...  # body unchanged

def test_init_derives_name_from_target_directory(tmp_path, settings):
    ...  # body unchanged

def test_init_scaffolds_pixi_toml_when_absent(tmp_path, settings):
    ...  # body unchanged

def test_init_does_not_overwrite_existing_pixi_toml(tmp_path, settings):
    ...  # body unchanged

def test_init_does_not_write_pixi_toml_when_pyproject_toml_exists(tmp_path, settings):
    ...  # body unchanged

def test_init_writes_sidecar_with_init_block(tmp_path, settings):
    ...  # body unchanged
```

(Only the signatures change - add `, settings` as the last parameter on each of these eight functions; every line in each function body stays exactly as it is today.)

- [ ] **Step 2: Write the new failing tests**

Append to `dvt/tests/test_init.py`:

```python
def _write_image_registry(settings, images):
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    for image in images:
        (settings.images_dir / f"{image['name']}.json").write_text(json.dumps(image))


def test_init_image_resolves_alias_via_cached_registry(tmp_path, settings):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": ["cuda"],
            }
        ],
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "cuda"])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_resolves_close_typo_with_confirm(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "bas-cuda"])

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_declining_confirm_writes_nothing(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    project_dir = tmp_path / "my-project"

    result = runner.invoke(app, ["init", str(project_dir), "--image", "bas-cuda"])

    assert result.exit_code == 1
    assert not (project_dir / ".devcontainer" / "devcontainer.json").exists()


def test_init_image_yes_flag_skips_the_prompt(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, ["init", str(project_dir), "--image", "bas-cuda", "--yes"]
    )

    assert result.exit_code == 0, result.output
    written = json.loads(
        (project_dir / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert written["image"] == "ghcr.io/jesserobertson/base-cuda:latest"


def test_init_image_json_mode_fails_with_suggestion_no_hang(tmp_path, settings, monkeypatch):
    _write_image_registry(
        settings,
        [
            {
                "name": "base-cuda",
                "description": "",
                "ref": "ghcr.io/jesserobertson/base-cuda:latest",
                "aliases": [],
            }
        ],
    )
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    project_dir = tmp_path / "my-project"

    result = runner.invoke(
        app, ["init", str(project_dir), "--image", "bas-cuda", "--json"]
    )

    assert result.exit_code == 1
    printed = json.loads(result.output)
    assert printed["ok"] is False
    assert "base-cuda" in printed["error"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pixi run pytest tests/test_init.py -v`
Expected: mixed failures — the Step 1 signature changes alone should already pass (they don't change behavior), but the new Step 2 tests fail: `--image cuda` currently writes `"image": "cuda"` verbatim (no resolution happens yet), and there's no `--yes` option yet (`Error: No such option: --yes`).

- [ ] **Step 4: Write the implementation**

In `dvt/src/devtemplate/commands/init.py`:

Add imports:

```python
from devtemplate.cli_support import emit_success, report_error, unwrap_or_exit
from devtemplate.config import load_settings
from devtemplate.images import resolve_image_ref
```

(This replaces the existing `from devtemplate.cli_support import emit_success, report_error` line - add `unwrap_or_exit` to it.)

Change the `init` function signature and body:

```python
def init(
    path: Path = typer.Argument(..., help="Project directory to scaffold."),  # noqa: B008
    image: str = typer.Option(  # noqa: B008
        DEFAULT_IMAGE, help=f"Base image (default: {DEFAULT_IMAGE})."
    ),
    assume_yes: bool = typer.Option(  # noqa: B008
        False,
        "--yes",
        "-y",
        help="Auto-accept a fuzzy-matched image name instead of prompting.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Scaffold a new project's devcontainer.json with no features yet."""
    name = path.resolve().name

    devcontainer_dir = path / ".devcontainer"
    target = devcontainer_dir / "devcontainer.json"
    if target.exists():
        report_error(
            f"{target} already exists. Use 'dvt feature add' to layer onto it instead.",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1)

    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    resolved_image = unwrap_or_exit(
        resolve_image_ref(
            image, settings, assume_yes=assume_yes, interactive=not json_output
        ),
        console,
        json_output=json_output,
    )

    config: dict[str, Any] = {
        "name": name,
        "image": resolved_image,
        "workspaceFolder": "/workspace",
        "workspaceMount": (
            "source=${localWorkspaceFolder},"
            "target=/workspace,type=bind,consistency=cached"
        ),
        "remoteUser": "dev",
        "postCreateCommand": POST_CREATE_COMMAND,
    }

    devcontainer_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n")

    sidecar_result = write_sidecar(devcontainer_dir, {"init": config, "applied": []})
    if sidecar_result.is_err() and not json_output:
        console.print(
            "[yellow]Warning: failed to write the feature-tracking sidecar: "
            f"{escape(str(sidecar_result.unwrap_err()))}[/yellow]"
        )

    scaffold_pixi_toml(path, name)

    emit_success(
        json_output,
        {"name": name, "path": str(target)},
        lambda: console.print(f"Scaffolded {target}."),
    )
```

(Only the `config["image"]` line and everything above it in the function body changed; the sidecar/pixi-toml/emit_success tail is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pixi run pytest tests/test_init.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Run the full dvt test suite**

Run: `pixi run pytest -v`
Expected: PASS (every test in `dvt/tests/`, confirming nothing in Tasks 1-12 regressed anything else)

- [ ] **Step 7: Commit**

```bash
git add dvt/src/devtemplate/commands/init.py dvt/tests/test_init.py
git commit -m "feat(dvt): fuzzy-resolve dvt init --image against the cached registry"
```

---

### Task 13: Repo content — `images/base-ubuntu.json`, `images/base-cuda.json`

**Files:**
- Create: `images/base-ubuntu.json` (repo root, sibling to `templates/`)
- Create: `images/base-cuda.json` (repo root)
- Modify: `tests/test_static.py` (repo root, not `dvt/tests/`)

**Interfaces:**
- Produces: the actual registry content `dvt image sync` fetches in real (non-test) use, matching the `ref` values every GPU/CPU template in `templates/*/devcontainer.json` already hardcodes (verified against `tests/test_static.py`'s `test_gpu_template_uses_base_cuda`/`test_cpu_template_uses_base_ubuntu`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_static.py` (repo root, run via plain `pytest` from the repo root - not `dvt/`'s pixi environment):

```python
IMAGES = ["base-ubuntu", "base-cuda"]


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


def _image_json(image: str) -> dict:
    path = REPO_ROOT / "images" / f"{image}.json"
    return json.loads(path.read_text())
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from the repo root): `pytest tests/test_static.py -v -k image`
Expected: FAIL — `FileNotFoundError`, `images/base-ubuntu.json` doesn't exist yet.

- [ ] **Step 3: Write the registry files**

```json
// images/base-ubuntu.json
{
  "name": "base-ubuntu",
  "description": "Ubuntu 24.04 devcontainer base with fish, homebrew, pixi, and dev CLI tooling.",
  "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
  "aliases": ["ubuntu", "default"]
}
```

```json
// images/base-cuda.json
{
  "name": "base-cuda",
  "description": "CUDA-enabled devcontainer base for GPU workloads.",
  "ref": "ghcr.io/jesserobertson/base-cuda:latest",
  "aliases": ["cuda", "gpu"]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_static.py -v`
Expected: PASS (every test in the file, including the pre-existing ones - confirms this addition didn't disturb anything)

- [ ] **Step 5: Commit**

```bash
git add images/base-ubuntu.json images/base-cuda.json tests/test_static.py
git commit -m "feat: add images/ registry entries for base-ubuntu and base-cuda"
```

---

## After all tasks

Run the full suite once more from both locations to confirm nothing drifted across the two test roots:

```bash
# from dvt/
pixi run check-all

# from the repo root
pytest tests/test_static.py -v
```

Nothing in this plan publishes anything - `images/*.json` and every `dvt/` change are local commits only until the user reviews and decides to push, consistent with how every other change in this session has been handled.
