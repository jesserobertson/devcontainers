# dvt Dependency Awareness v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `dvt` show users the feature dependency graph — `dvt sync` pre-pulls every known feature's OCI artifact into the build cache, and four surfaces read it: a "Pulls in" column in `dvt feature list`, a dependency tree in `dvt feature show`, a new `dvt feature deps` command, and an "also pulling in" line at `dvt feature add`.

**Architecture:** Reuse the shipped `devtemplate.feature_graph` (its `FeatureSpec` / `read_feature_spec` / `resolve_feature_graph`) rather than a parallel spec cache. Add two pure, network-free helpers to that module (`describe_graph` over already-read `FeatureSpec`s, plus `ref_to_id` and `load_cached_specs`). `dvt sync` gains a step that pulls the OCI artifact for every feature ref its cached templates reference, following `dependsOn` transitively via `resolve_feature_graph`, into the existing `settings.features_dir` cache. The `feature` command module consumes `load_cached_specs` + `describe_graph` for all four surfaces; nothing fetches at command time.

**Tech Stack:** Python 3.12+, Typer (`devtemplate.describe.Typer`), Rich (`Table`, `Tree`), httpx, `logerr` (`Result`/`Ok`/`Err`/`wrap_result`), pytest with the existing `tests/test_feature_graph.py::FakeRegistry` fixture pattern.

**Spec:** `docs/superpowers/specs/2026-09-03-dvt-dependency-awareness-v2.md`

## Global Constraints

- All work is under `dvt/` (run every command from `dvt/`). Package import root is `devtemplate`. Run tests with `pixi run -e dev pytest ...`, quality with `pixi run -e dev quality check` (mypy + ruff check + ruff format — all must pass).
- Conventions: `from __future__ import annotations`, explicit `__all__`, `logerr` `Result`/`Ok`/`Err`/`wrap_result`, `@dataclass(frozen=True)`, full mypy strict.
- The OCI pull cache is `settings.features_dir` (`<data_dir>/features`), populated by `devtemplate.features.pull_feature` (keyed by `sha256(ref)`) and wiped by `clear_pulled_features` at the top of `dvt sync`. Do **not** introduce a second cache directory or manifest.
- `feature_graph` API already present: `FeatureSpec(id, depends_on: tuple[str,...], installs_after: tuple[str,...], container_env: dict[str,str])` — refs already `normalise_ref`d; `read_feature_spec(extracted_dir: Path) -> FeatureSpec` (raises on missing/malformed); `resolve_feature_graph(explicit: dict[str, dict[str,str]], pull: Callable[[str], Path]) -> Result[list[ResolvedFeature], Exception]`; `normalise_ref(ref)`; module-private `_find_cycle`.
- Short-id derivation is `ref.rsplit("/", 1)[-1].split(":")[0]` — currently `feature_id` in `workspace/up.py`. This plan moves it to `feature_graph.ref_to_id` and re-imports it in `up.py` (no behaviour change).
- CLI option name for JSON output is `json_output` everywhere (matches every existing command). The new `--format` option binds to a param named `fmt` (avoid shadowing `format`).
- `dvt feature list` / `dvt feature add` must never hard-fail when the feature cache is empty or a cycle is present — degrade to `—` / a stderr hint.
- `dvt sync` pre-pull failures are per-ref warnings, never a sync abort.
- `dvt` version → `0.5.0` (`dvt/pyproject.toml`), one `dvt/CHANGELOG.md` entry.
- Commit after every green step; stage explicit paths, never `git add -A`. Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01C3rVpkRa9o8uC7F73bKTku
  ```

---

## File Structure

**Modified:**
- `dvt/src/devtemplate/feature_graph.py` — add `ref_to_id`, `load_cached_specs`, `GraphNode`, `describe_graph`, `to_dot`, `to_mermaid`; extend `__all__`
- `dvt/src/devtemplate/workspace/up.py` — replace local `feature_id` with `from devtemplate.feature_graph import ref_to_id` (keep call sites working)
- `dvt/src/devtemplate/cli.py` — `sync`'s `do_sync` pre-pulls feature refs; human summary + return dict gain `feature_specs`
- `dvt/src/devtemplate/cli_output_schemas.py` — `SyncOutput` gains `feature_specs: list[str]`
- `dvt/src/devtemplate/commands/feature.py` — `list_features` column; `show_feature` tree + `--json` key; new `deps` command (+ `tree` alias); `add` message; drop the never-written `pulls_in` sidecar idea (it was never implemented — nothing to remove, just don't add it)
- `dvt/pyproject.toml` — `version = "0.5.0"`
- `dvt/CHANGELOG.md`, `dvt/README.md`, `dvt/docs/content/quickstart.md`
- `dvt/tests/test_feature_graph.py`, `dvt/tests/test_cli.py`, `dvt/tests/test_feature_command.py` — extend

**Created:**
- `dvt/tests/test_feature_deps_command.py`

---

## Task 1: `ref_to_id` + `load_cached_specs` in `feature_graph.py`

**Files:**
- Modify: `dvt/src/devtemplate/feature_graph.py`
- Modify: `dvt/src/devtemplate/workspace/up.py`
- Modify: `dvt/tests/test_feature_graph.py`

**Interfaces:**
- Produces: `ref_to_id(ref: str) -> str` — `"ghcr.io/x/pixi:latest"` → `"pixi"`; strips `@sha256:…` first, then `:tag`, then last path segment. `load_cached_specs(settings: Settings) -> dict[str, FeatureSpec]` — every readable `devcontainer-feature.json` under `settings.features_dir/*/`, keyed by `FeatureSpec.id`; `{}` when the dir is absent; a subdir whose `read_feature_spec` raises is skipped (not fatal).
- Consumes: existing `read_feature_spec`, `Settings`.
- Consumed by: Tasks 2, 3, 4, 5, 6, 7.

- [ ] **Step 1: Write failing tests**

Add to `dvt/tests/test_feature_graph.py` (the `reg: FakeRegistry` fixture builds `tmp_path` feature dirs — reuse it; `settings` needs `features_dir` pointed at the fixture root, so build a tiny `Settings` stand-in or `monkeypatch` `Settings.data_dir`):

```python
from devtemplate.feature_graph import ref_to_id, load_cached_specs


def test_ref_to_id_strips_tag_digest_and_path():
    assert ref_to_id("ghcr.io/jesserobertson/devcontainers/pixi:latest") == "pixi"
    assert ref_to_id("ghcr.io/x/pixi@sha256:abc123") == "pixi"
    assert ref_to_id("ghcr.io/x/pixi") == "pixi"


def test_load_cached_specs_reads_every_subdir_keyed_by_id(tmp_path, monkeypatch):
    from devtemplate.config import Settings
    feats = tmp_path / "features"
    for name, dep in (("homebrew", None), ("pixi", None), ("py-devtools", "homebrew")):
        d = feats / f"hash-{name}"
        d.mkdir(parents=True)
        spec = {"id": name, "version": "1.0.0"}
        if dep:
            spec["dependsOn"] = {f"ghcr.io/jesserobertson/devcontainers/{dep}": {}}
        (d / "devcontainer-feature.json").write_text(json.dumps(spec))
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    specs = load_cached_specs(Settings())
    assert set(specs) == {"homebrew", "pixi", "py-devtools"}
    assert specs["py-devtools"].depends_on == (
        "ghcr.io/jesserobertson/devcontainers/homebrew:latest",
    )


def test_load_cached_specs_empty_when_dir_absent(tmp_path, monkeypatch):
    from devtemplate.config import Settings
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    assert load_cached_specs(Settings()) == {}


def test_load_cached_specs_skips_malformed_dir(tmp_path, monkeypatch):
    from devtemplate.config import Settings
    feats = tmp_path / "features"
    (feats / "hash-ok").mkdir(parents=True)
    (feats / "hash-ok" / "devcontainer-feature.json").write_text('{"id": "ok"}')
    (feats / "hash-bad").mkdir(parents=True)
    (feats / "hash-bad" / "devcontainer-feature.json").write_text("{ not json")
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    assert set(load_cached_specs(Settings())) == {"ok"}
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_graph.py -k "ref_to_id or load_cached_specs" -v`
Expected: FAIL — `cannot import name 'ref_to_id'`.

- [ ] **Step 3: Implement in `feature_graph.py`**

```python
def ref_to_id(ref: str) -> str:
    """Short id from an OCI ref's trailing path segment:
    'ghcr.io/x/pixi:latest' -> 'pixi'. Strips an '@sha256:…' digest first, then
    a ':tag', then takes the last path segment."""
    body = ref.split("@", 1)[0]
    body = body.rsplit(":", 1)[0] if ":" in body.rsplit("/", 1)[-1] else body
    return body.rsplit("/", 1)[-1]


def load_cached_specs(settings: Settings) -> dict[str, FeatureSpec]:
    """Every readable devcontainer-feature.json under settings.features_dir/*/,
    keyed by FeatureSpec.id. {} when the directory is absent. A subdir whose
    read_feature_spec raises is skipped."""
    directory = settings.features_dir
    if not directory.exists():
        return {}
    specs: dict[str, FeatureSpec] = {}
    for child in sorted(p for p in directory.iterdir() if p.is_dir()):
        try:
            spec = read_feature_spec(child)
        except (OSError, ValueError, KeyError):
            continue
        specs[spec.id] = spec
    return specs
```

Add `from devtemplate.config import Settings` (import, not TYPE_CHECKING — it's used at runtime). Add `"ref_to_id"`, `"load_cached_specs"` to `__all__`.

- [ ] **Step 4: Re-point `up.py`**

In `dvt/src/devtemplate/workspace/up.py`: delete the local `def feature_id(ref: str) -> str:` and its docstring; add `ref_to_id` to the existing `from devtemplate.feature_graph import ...` line; replace every `feature_id(` call site in that file with `ref_to_id(`.

- [ ] **Step 5: Run — expect pass**

Run: `pixi run -e dev pytest tests/test_feature_graph.py tests/test_cli.py -q` (test_cli exercises the `up` path)
Expected: PASS. Then `pixi run -e dev quality check` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/devtemplate/feature_graph.py src/devtemplate/workspace/up.py tests/test_feature_graph.py
git commit -m "feat(dvt): ref_to_id + load_cached_specs in feature_graph"
```

---

## Task 2: `describe_graph` + `GraphNode` + emitters

**Files:**
- Modify: `dvt/src/devtemplate/feature_graph.py`
- Modify: `dvt/tests/test_feature_graph.py`

**Interfaces:**
- Produces:
  - `GraphNode` (frozen): `id: str`, `pulls_in: tuple[str, ...]` (transitive `dependsOn` closure among the given specs, as short ids, sorted+deduped; a ref outside the spec set kept as its bare normalised ref), `installs_after: tuple[str, ...]` (this feature's own `installsAfter` mapped to ids where known, bare ref otherwise; NOT transitive).
  - `describe_graph(specs: Mapping[str, FeatureSpec]) -> Result[dict[str, GraphNode], Exception]` — one node per spec; `Err(ValueError("feature dependency cycle: …"))` on a `dependsOn` cycle (reuse `_find_cycle`).
  - `to_dot(nodes: Iterable[GraphNode]) -> str`, `to_mermaid(nodes: Iterable[GraphNode]) -> str` — deterministic (sorted edges); `A -> B` / `A --> B` means "A pulls in B".
- Consumes: `FeatureSpec`, `ref_to_id`, `_find_cycle`.
- Consumed by: Tasks 4, 5, 6, 7.

- [ ] **Step 1: Write failing tests**

```python
from devtemplate.feature_graph import (
    FeatureSpec, GraphNode, describe_graph, to_dot, to_mermaid,
)

PFX = "ghcr.io/jesserobertson/devcontainers"


def _spec(id_, deps=(), after=()):
    return FeatureSpec(
        id=id_,
        depends_on=tuple(f"{PFX}/{d}:latest" for d in deps),
        installs_after=tuple(f"{PFX}/{a}:latest" for a in after),
        container_env={},
    )


def test_describe_graph_direct_and_transitive_closure():
    specs = {
        "homebrew": _spec("homebrew"),
        "shell-kit": _spec("shell-kit", deps=["homebrew"]),
        "pixi": _spec("pixi", after=["homebrew", "shell-kit"]),
        "big": _spec("big", deps=["shell-kit", "pixi"]),
    }
    nodes = describe_graph(specs).unwrap()
    assert nodes["big"].pulls_in == ("homebrew", "pixi", "shell-kit")
    assert nodes["pixi"].pulls_in == ()           # installsAfter is not a pull-in
    assert nodes["pixi"].installs_after == ("homebrew", "shell-kit")
    assert nodes["shell-kit"].pulls_in == ("homebrew",)


def test_describe_graph_ref_outside_set_kept_bare():
    specs = {"x": _spec("x", deps=["common-utils"])}  # common-utils not in specs
    node = describe_graph(specs).unwrap()["x"]
    assert node.pulls_in == ("common-utils",) or node.pulls_in == (
        f"{PFX}/common-utils:latest",
    )


def test_describe_graph_cycle_is_err():
    specs = {"a": _spec("a", deps=["b"]), "b": _spec("b", deps=["a"])}
    assert describe_graph(specs).is_err()


def test_to_dot_and_mermaid_are_deterministic():
    specs = {"rapids": _spec("rapids", deps=["pixi"]), "pixi": _spec("pixi")}
    nodes = list(describe_graph(specs).unwrap().values())
    dot = to_dot(nodes)
    assert dot.startswith("digraph")
    assert '"rapids" -> "pixi";' in dot
    mmd = to_mermaid(nodes)
    assert mmd.startswith("graph TD")
    assert "rapids --> pixi" in mmd
    assert to_dot(nodes) == to_dot(list(reversed(nodes)))  # order-independent
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_graph.py -k "describe_graph or to_dot or to_mermaid" -v`
Expected: FAIL — `cannot import name 'GraphNode'`.

- [ ] **Step 3: Implement**

```python
@dataclass(frozen=True)
class GraphNode:
    id: str
    pulls_in: tuple[str, ...]
    installs_after: tuple[str, ...]


def _label(ref: str, ids: set[str]) -> str:
    ident = ref_to_id(ref)
    return ident if ident in ids else ref


def describe_graph(
    specs: Mapping[str, FeatureSpec]
) -> Result[dict[str, GraphNode], Exception]:
    def _describe() -> dict[str, GraphNode]:
        ids = set(specs)
        # id -> set of dependsOn labels (ids where known, bare ref otherwise)
        edges: dict[str, list[str]] = {}
        for fid, spec in specs.items():
            edges[fid] = [_label(r, ids) for r in spec.depends_on]

        cycle = _find_cycle({k: [e for e in v if e in specs] for k, v in edges.items()})
        if cycle is not None:
            raise ValueError("feature dependency cycle: " + " -> ".join(cycle))

        def closure(fid: str) -> set[str]:
            seen: set[str] = set()
            stack = list(edges.get(fid, ()))
            while stack:
                dep = stack.pop()
                if dep in seen:
                    continue
                seen.add(dep)
                if dep in specs:
                    stack.extend(edges[dep])
            return seen

        nodes: dict[str, GraphNode] = {}
        for fid, spec in specs.items():
            nodes[fid] = GraphNode(
                id=fid,
                pulls_in=tuple(sorted(closure(fid))),
                installs_after=tuple(_label(r, ids) for r in spec.installs_after),
            )
        return nodes

    return execute(_describe)  # execute already imported for resolve_feature_graph
```

Check `_find_cycle`'s actual signature in the file and adapt the call (it may take the adjacency map directly, or a different shape — match it; the intent is "detect a cycle in the dependsOn edges restricted to known nodes"). If `_find_cycle` returns a list-or-None, the code above is right; if it raises, wrap accordingly.

```python
def to_dot(nodes: Iterable[GraphNode]) -> str:
    lines = ["digraph deps {"]
    for src, dst in _edges(nodes):
        lines.append(f'  "{src}" -> "{dst}";')
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(nodes: Iterable[GraphNode]) -> str:
    lines = ["graph TD"]
    for src, dst in _edges(nodes):
        lines.append(f"  {src} --> {dst}")
    return "\n".join(lines)


def _edges(nodes: Iterable[GraphNode]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    for n in nodes:
        for dep in n.pulls_in:
            seen.add((n.id, dep))
    return sorted(seen)
```

Add `GraphNode`, `describe_graph`, `to_dot`, `to_mermaid` to `__all__`. `Mapping` from `collections.abc`.

- [ ] **Step 4: Run — expect pass**

Run: `pixi run -e dev pytest tests/test_feature_graph.py -q && pixi run -e dev quality check`
Expected: PASS + clean.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/feature_graph.py tests/test_feature_graph.py
git commit -m "feat(dvt): describe_graph + dot/mermaid emitters in feature_graph"
```

---

## Task 3: `dvt sync` pre-pulls the feature cache

**Files:**
- Modify: `dvt/src/devtemplate/cli.py`
- Modify: `dvt/src/devtemplate/cli_output_schemas.py`
- Modify: `dvt/tests/test_cli.py`

**Interfaces:**
- Produces: `sync`'s `do_sync` returns `{"features": ..., "images": ..., "feature_specs": [...]}`. Human output gains `Synced N feature specs: …`. `SyncOutput` gains `feature_specs: list[str]`.
- Consumes: `store.list_cached_templates` / `load_cached_template`, `feature_graph.resolve_feature_graph`, `feature_graph.load_cached_specs`, `features.pull_feature`, `images.find_repo_root` (optional).

- [ ] **Step 1: Update `SyncOutput` + a failing test**

`cli_output_schemas.py`:
```python
class SyncOutput(BaseModel):
    ok: Literal[True]
    features: list[str]
    images: list[str]
    feature_specs: list[str]
```

In `dvt/tests/test_cli.py` find the `sync --json` shape test; add `"feature_specs"` to the expected key set and assert it lists the ids for the mocked templates' feature refs. Add a test that a `pull_feature` raising for ONE ref leaves the others synced and emits no exception (patch `pull_feature` to raise for a chosen ref, succeed for the rest; assert `sync` exits 0 and `feature_specs` omits only the failed id). Match `test_cli.py`'s existing mocking style (it already stubs `sync_templates` / `sync_images` / httpx).

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_cli.py -k sync -v`
Expected: FAIL — schema/key mismatch.

- [ ] **Step 3: Implement in `cli.py`**

Add imports: `from devtemplate.feature_graph import load_cached_specs, resolve_feature_graph`, `from devtemplate.features import pull_feature` (if not already), `from devtemplate.store import list_cached_templates, load_cached_template`.

In `do_sync`, after `images = sync_images(settings, client).unwrap()`:

```python
        # Pre-pull the OCI artifact for every feature ref the cached templates
        # reference (plus transitive dependsOn), into settings.features_dir - the
        # same cache `dvt up` and the graph views read. Per-ref failures warn.
        refs: list[str] = []
        for name in list_cached_templates(settings):
            tmpl = load_cached_template(settings, name).unwrap_or({})
            refs.extend(tmpl.get("features", {}).keys())
        for base_cfg in _bundle_plumbing_refs(settings):  # see helper below
            refs.append(base_cfg)

        def _pull(ref: str) -> Path:
            return pull_feature(client, ref, settings.features_dir).unwrap()

        for ref in dict.fromkeys(refs):  # dedupe, keep order
            result = resolve_feature_graph({ref: {}}, _pull)
            if result.is_err():
                stderr_console.print(
                    f"[yellow]sync: could not pull feature {ref!r}: "
                    f"{result.unwrap_err()}[/yellow]"
                )

        feature_specs = sorted(load_cached_specs(settings))
    return {"features": features, "images": images, "feature_specs": feature_specs}
```

`_bundle_plumbing_refs(settings)` helper (module-level in `cli.py`): try `images.find_repo_root(Path.cwd())`; on success read
`<root>/images/base-ubuntu/.devcontainer/devcontainer.json` and
`<root>/images/base-cuda/.devcontainer/devcontainer.json`, return the union of
their `features` map keys; on `FileNotFoundError` (no checkout) return `[]`.
Wrap in `try/except` so it never raises.

Human summary lambda gains:
```python
            f"\nSynced {len(synced['feature_specs'])} feature specs: "
            f"{', '.join(synced['feature_specs'])}"
```

Note: `resolve_feature_graph` here is used only for its transitive-pull side effect (each call pulls `ref` + its `dependsOn` targets into `features_dir`); its ordered return is intentionally discarded. Calling it once per ref (rather than once with all refs) isolates a per-ref pull failure.

- [ ] **Step 4: Run — expect pass**

Run: `pixi run -e dev pytest tests/test_cli.py -k sync -q && pixi run -e dev quality check`
Expected: PASS + clean.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/cli.py src/devtemplate/cli_output_schemas.py tests/test_cli.py
git commit -m "feat(dvt): dvt sync pre-pulls each known feature's artifact"
```

---

## Task 4: `dvt feature list` — "Pulls in" column

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py`
- Modify: `dvt/tests/test_feature_command.py`

**Interfaces:**
- `list_features` table gains a **"Pulls in"** column between `Description` and `Base Image`: `", ".join(nodes[name].pulls_in)` or `"—"` when the name is absent from `nodes` / the cache is empty / `describe_graph` errored. `--json` rows gain `"pulls_in": list[str]` (`[]` when unavailable). Empty cache → all `"—"` + one dim stderr line `run 'dvt sync' for dependency info` (non-JSON only).

- [ ] **Step 1: Write failing tests**

In `dvt/tests/test_feature_command.py` (it already tests `list_features`; reuse its settings/cache fixtures — you'll need a populated `features_dir`, so add a fixture that drops two feature dirs `rapids` (dependsOn pixi) + `pixi` under `settings.features_dir`):

```python
def test_list_shows_pulls_in_column(list_env_with_dep_cache, capsys):
    list_features(json_output=False)
    out = capsys.readouterr().out
    assert "Pulls in" in out
    rapids_line = next(l for l in out.splitlines() if "rapids" in l)
    assert "pixi" in rapids_line


def test_list_json_has_pulls_in(list_env_with_dep_cache, capsys):
    list_features(json_output=True)
    rows = json.loads(capsys.readouterr().out)
    assert next(r for r in rows if r["name"] == "rapids")["pulls_in"] == ["pixi"]


def test_list_cold_cache_degrades(list_env_no_dep_cache, capsys):
    list_features(json_output=False)
    captured = capsys.readouterr()
    assert "Pulls in" in captured.out and "—" in captured.out
    assert "run 'dvt sync' for dependency info" in captured.err
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_command.py -k "pulls_in or cold_cache" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `from devtemplate.feature_graph import load_cached_specs, describe_graph`.

In `list_features`, after `settings` is loaded:
```python
    specs = load_cached_specs(settings)
    nodes = describe_graph(specs).unwrap_or({})
```
In the per-name row build, add `"pulls_in": list(nodes[name].pulls_in) if name in nodes else []`.
JSON branch: unchanged (rows already carry the key).
Table branch:
```python
    table = Table("Name", "Description", "Pulls in", "Base Image")
    for row in rows:
        table.add_row(
            row["name"], row["description"],
            ", ".join(row["pulls_in"]) or "—", row["image"],
        )
    console.print(table)
    if not nodes:
        stderr_console.print("[dim]run 'dvt sync' for dependency info[/dim]")
```

- [ ] **Step 4: Run — expect pass; fix any existing 3-column list test**

Run: `pixi run -e dev pytest tests/test_feature_command.py -q && pixi run -e dev quality check`
If an existing test asserts the old header/columns, update it to 4 columns.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "feat(dvt): dvt feature list shows a Pulls in column"
```

---

## Task 5: `dvt feature show` — dependency tree + `--json` key

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py`
- Modify: `dvt/tests/test_feature_command.py`

**Interfaces:**
- Non-JSON: after the overlay JSON, if `name in specs`, print a `rich.tree.Tree` rooted at `name` whose children are the `dependsOn` subtree recursively (built from `specs`, so it shows structure, not the flat closure); a node with `installsAfter` gets ` [dim](after: x, y)[/dim]`.
- `--json`: when `name in nodes`, emit `json.dumps({**template, "resolved_depends_on": list(nodes[name].pulls_in)}, indent=2)`; else the raw overlay unchanged.

- [ ] **Step 1: Write failing tests**

```python
def test_show_prints_dependency_tree(show_env_with_dep_cache, capsys):
    show_feature(name="rapids", json_output=False)
    out = capsys.readouterr().out
    assert "rapids" in out and "pixi" in out


def test_show_json_has_resolved_depends_on(show_env_with_dep_cache, capsys):
    show_feature(name="rapids", json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved_depends_on"] == ["pixi"]


def test_show_uncached_name_still_prints_overlay(show_env_no_dep_cache, capsys):
    show_feature(name="cli", json_output=False)
    assert '"features"' in capsys.readouterr().out  # overlay still printed, no crash
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_command.py -k show -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add `from rich.tree import Tree`. Helper in `feature.py`:
```python
def _dep_tree(fid: str, specs: Mapping[str, FeatureSpec]) -> Tree:
    def add(node_id: str, parent: Tree) -> None:
        spec = specs.get(node_id)
        suffix = ""
        if spec and spec.installs_after:
            labels = ", ".join(ref_to_id(r) for r in spec.installs_after)
            suffix = f" [dim](after: {labels})[/dim]"
        branch = parent.add(f"{node_id}{suffix}")
        for ref in (spec.depends_on if spec else ()):
            add(ref_to_id(ref), branch)

    root = Tree(fid)
    for ref in specs[fid].depends_on:
        add(ref_to_id(ref), root)
    return root
```

In `show_feature`, after loading `template`:
```python
    specs = load_cached_specs(settings)
    nodes = describe_graph(specs).unwrap_or({})
    if json_output:
        if name in nodes:
            print(json.dumps(
                {**template, "resolved_depends_on": list(nodes[name].pulls_in)},
                indent=2,
            ))
        else:
            print(json.dumps(template, indent=2))
        return
    print(json.dumps(template, indent=2))
    if name in specs:
        console.print(_dep_tree(name, specs))
```
(Replace the current unconditional `print(json.dumps(template, indent=2))` with the above.)

- [ ] **Step 4: Run — expect pass**

Run: `pixi run -e dev pytest tests/test_feature_command.py -q && pixi run -e dev quality check`

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "feat(dvt): dvt feature show renders the dependency tree"
```

---

## Task 6: `dvt feature deps` command

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py`
- Create: `dvt/tests/test_feature_deps_command.py`
- Modify: `dvt/src/devtemplate/cli_output_schemas.py` (only if it maps per-command output schemas by name — check)

**Interfaces:**
- `dvt feature deps [NAME] [--format tree|dot|mermaid] [--json]`, plus `dvt feature tree` as an alias registered via `app.command("tree", hidden=True)(deps)`.
- `NAME` given: fuzzy-resolved against `load_cached_specs(...).keys()` via `resolve_or_confirm` (as `show` does). No `NAME`: whole fleet.
- `--format tree` (default): a `_dep_tree` per feature that has any `depends_on`. `dot` / `mermaid`: `to_dot` / `to_mermaid` over the selected `GraphNode`s. `--json`: `{ "<id>": {"pulls_in": [...], "installs_after": [...]}, ... }`.
- Empty cache: exit 0, `No feature dependency data. Run 'dvt sync' first.` on stderr (non-JSON) / `{}` (JSON).

- [ ] **Step 1: Write `test_feature_deps_command.py`**

```python
import json
from devtemplate.commands.feature import deps as deps_cmd


def test_deps_single_tree(deps_env_with_cache, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=False)
    out = capsys.readouterr().out
    assert "rapids" in out and "pixi" in out


def test_deps_single_json(deps_env_with_cache, capsys):
    deps_cmd(name="rapids", fmt="tree", json_output=True)
    assert json.loads(capsys.readouterr().out) == {
        "rapids": {"pulls_in": ["pixi"], "installs_after": []}
    }


def test_deps_fleet_dot(deps_env_with_cache, capsys):
    deps_cmd(name=None, fmt="dot", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("digraph") and '"rapids" -> "pixi";' in out


def test_deps_fleet_mermaid(deps_env_with_cache, capsys):
    deps_cmd(name=None, fmt="mermaid", json_output=False)
    out = capsys.readouterr().out
    assert out.startswith("graph TD") and "rapids --> pixi" in out


def test_deps_empty_cache(deps_env_no_cache, capsys):
    deps_cmd(name=None, fmt="tree", json_output=True)
    assert json.loads(capsys.readouterr().out) == {}
```
(Build `deps_env_*` fixtures the same way as Tasks 4/5 — populated vs empty `settings.features_dir`. Match the actual `deps` param names once implemented.)

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_deps_command.py -v`
Expected: FAIL — `cannot import name 'deps'`.

- [ ] **Step 3: Implement in `feature.py`**

```python
@app.command("deps")
def deps(
    name: str | None = typer.Argument(  # noqa: B008
        None, help="Feature to inspect; omit for the whole fleet."
    ),
    fmt: str = typer.Option(  # noqa: B008
        "tree", "--format", help="Output format: tree | dot | mermaid."
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False, "--json", help="Machine-readable JSON."
    ),
) -> None:
    """Show what each feature pulls in via dependsOn."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    specs = load_cached_specs(settings)
    nodes = unwrap_or_exit(describe_graph(specs), console, json_output=json_output)

    if not nodes:
        if json_output:
            print(json.dumps({}))
        else:
            stderr_console.print("No feature dependency data. Run 'dvt sync' first.")
        raise typer.Exit(code=0)

    if name is not None:
        resolved = unwrap_or_exit(
            resolve_or_confirm(
                name, sorted(specs), label="feature",
                assume_yes=json_output, interactive=not json_output,
            ),
            console, json_output=json_output,
        )
        targets = [resolved]
    else:
        targets = sorted(specs)

    if json_output:
        print(json.dumps({
            t: {
                "pulls_in": list(nodes[t].pulls_in),
                "installs_after": list(nodes[t].installs_after),
            }
            for t in targets
        }))
        return

    selected = [nodes[t] for t in targets]
    if fmt == "dot":
        print(to_dot(selected))
    elif fmt == "mermaid":
        print(to_mermaid(selected))
    else:
        for t in targets:
            if specs[t].depends_on:
                console.print(_dep_tree(t, specs))


app.command("tree", hidden=True)(deps)
```
Add imports for `to_dot`, `to_mermaid`. If `describe_graph` errors (cycle), `unwrap_or_exit` already prints and exits — acceptable for `deps` (unlike `list`/`add`, a broken graph is the whole point of this command failing loudly).

If `cli_output_schemas.py` has a name-keyed map (e.g. `{"sync": SyncOutput, ...}`), add `"feature deps"` / `"deps"` per its convention with a permissive `RootModel[dict[str, Any]]`.

- [ ] **Step 4: Run — expect pass + describe/fuzzy regression check**

Run: `pixi run -e dev pytest tests/test_feature_deps_command.py tests/ -k "describe or fuzzy or feature" -q && pixi run -e dev quality check`
Expected: PASS — the new subcommand must not break `--describe` scoping or fuzzy `--yes` injection.

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py src/devtemplate/cli_output_schemas.py tests/test_feature_deps_command.py
git commit -m "feat(dvt): add dvt feature deps (tree/dot/mermaid/json)"
```

---

## Task 7: `dvt feature add` — "also pulling in" message

**Files:**
- Modify: `dvt/src/devtemplate/commands/feature.py`
- Modify: `dvt/tests/test_feature_command.py`

**Interfaces:**
- In `add` (not `add_one`), after `resolved` is known: `nodes = describe_graph(load_cached_specs(settings)).unwrap_or({})`; if `resolved in nodes` and `nodes[resolved].pulls_in` and not `json_output`, print `also pulling in: <a, b> (via dependsOn)` to `console`. No sidecar change; `devcontainer.json` not modified for implied features.

- [ ] **Step 1: Write failing tests**

```python
def test_add_reports_pulled_in_deps(project_with_devcontainer, add_env_with_cache, capsys):
    add(names=["rapids"], assume_yes=True, json_output=False)
    out = capsys.readouterr().out
    assert "also pulling in" in out and "pixi" in out


def test_add_does_not_write_pulls_in_to_sidecar(project_with_devcontainer, add_env_with_cache):
    add(names=["rapids"], assume_yes=True, json_output=False)
    sidecar = json.loads(
        (project_with_devcontainer / ".devcontainer" / "dvt-features.json").read_text()
    )
    entry = next(e for e in sidecar["applied"] if e["name"] == "rapids")
    assert "pulls_in" not in entry


def test_add_does_not_inject_pixi_into_devcontainer_json(project_with_devcontainer, add_env_with_cache):
    add(names=["rapids"], assume_yes=True, json_output=False)
    cfg = json.loads(
        (project_with_devcontainer / ".devcontainer" / "devcontainer.json").read_text()
    )
    assert not any("pixi" in k for k in cfg["features"])
```

- [ ] **Step 2: Run — expect failure**

Run: `pixi run -e dev pytest tests/test_feature_command.py -k "add and (pulled_in or pulls_in or inject)" -v`
Expected: FAIL on the message assertion (the other two may already pass — that's fine, they're guardrails).

- [ ] **Step 3: Implement**

In `add`, right after `resolved` is obtained and before/after the `add_one(...)` call:
```python
        nodes = describe_graph(load_cached_specs(settings)).unwrap_or({})
        pulled = list(nodes[resolved].pulls_in) if resolved in nodes else []
        if pulled and not json_output:
            console.print(
                f"also pulling in: {', '.join(pulled)} (via dependsOn)"
            )
```
Leave `add_one` and the sidecar shape untouched.

- [ ] **Step 4: Run — expect pass**

Run: `pixi run -e dev pytest tests/test_feature_command.py -q && pixi run -e dev quality check`

- [ ] **Step 5: Commit**

```bash
git add src/devtemplate/commands/feature.py tests/test_feature_command.py
git commit -m "feat(dvt): dvt feature add reports dependsOn pull-ins"
```

---

## Task 8: Docs + version bump + full verification

**Files:**
- Modify: `dvt/pyproject.toml`, `dvt/CHANGELOG.md`, `dvt/README.md`, `dvt/docs/content/quickstart.md`

- [ ] **Step 1: `dvt/pyproject.toml`** — set `version = "0.5.0"`.

- [ ] **Step 2: `dvt/CHANGELOG.md`** — new `## [0.5.0]` section:
  - `dvt sync` now also pre-pulls each known feature's OCI artifact into the local cache.
  - `dvt feature list` gains a "Pulls in" column (transitive `dependsOn`).
  - `dvt feature show` renders the dependency tree; `--json` gains `resolved_depends_on`.
  - New `dvt feature deps [name]` command — `--format tree|dot|mermaid`, `--json`, single or whole-fleet.
  - `dvt feature add` prints what a feature pulls in via `dependsOn`.
  - Note: `dvt` does not inject implied features into `devcontainer.json` — its builder already resolves `dependsOn` at image-build time (`feature_graph`); these views just surface it. Run `dvt sync` once after upgrading to populate the column.

- [ ] **Step 3: `dvt/README.md` + `dvt/docs/content/quickstart.md`** — add `dvt feature deps [name]` to the command list/reference; mention the "Pulls in" column under `dvt feature list`; the "run `dvt sync` once after upgrading" note.

- [ ] **Step 4: Full verification**

Run:
- `pixi run -e dev quality check` → mypy + ruff + format all pass
- `pixi run -e dev test all` (or `pixi run -e dev pytest tests/ -q`) → all green
- `pixi run -e dev docs build --strict` if the repo checks it → no broken links from the new command reference

- [ ] **Step 5: Commit + (post-merge) tag**

```bash
git add dvt/pyproject.toml dvt/CHANGELOG.md dvt/README.md dvt/docs/
git commit -m "docs(dvt): document feature dependency views; bump to 0.5.0"
```
Post-merge: push `dvt-v0.5.0` (triggers `publish-dvt.yml` TestPyPI + `release-dvt.yml`); real-PyPI stays a manual `workflow_dispatch`.

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| `dvt sync` pre-populate the feature cache (collect refs, transitive pull, report) | Task 3 |
| `SyncOutput.feature_specs` | Task 3 |
| `feature_graph` `GraphNode` + `describe_graph` + `to_dot`/`to_mermaid` | Task 2 |
| `feature_graph.ref_to_id` (factored from `up.py`) | Task 1 |
| `feature_graph.load_cached_specs` | Task 1 |
| `dvt feature list` "Pulls in" column + `--json` + cold-cache degrade | Task 4 |
| `dvt feature show` tree + `--json` `resolved_depends_on` + uncached passthrough | Task 5 |
| `dvt feature deps` command + `tree` alias + formats + fleet + empty-cache | Task 6 |
| `dvt feature add` message, no sidecar/json rewrite | Task 7 |
| Docs + `0.5.0` + tag | Task 8 |
| Non-goals (no 2nd cache, no `github.py` fetchers, no `deps.py`, no injection) | Honoured — no task creates any of them |
| Backward compat (cold cache degrades, `dvt up` unaffected, `SyncOutput` additive) | Tasks 3–7 degradation paths |

**2. Placeholder scan** — every code step carries literal content. Two "check the actual signature and adapt" notes (Task 2's `_find_cycle` call shape; Task 6's `cli_output_schemas` map) are real verification steps against code the implementer holds, not placeholders. Fixture names (`list_env_with_dep_cache` etc.) are described (populated vs empty `settings.features_dir`) and the implementer builds them in the `FakeRegistry` style already in `test_feature_graph.py`.

**3. Type/name consistency**
- `ref_to_id` / `load_cached_specs` defined once (Task 1), imported by Tasks 2–7.
- `GraphNode` fields (`id`, `pulls_in`, `installs_after`) identical across Task 2's definition, the emitters, and Tasks 4–7's readers.
- `describe_graph(specs) -> Result[dict[str, GraphNode], Exception]` — same signature everywhere; every caller uses `.unwrap_or({})` except `deps` (Task 6) which `unwrap_or_exit`s deliberately.
- `_dep_tree(fid, specs)` defined in Task 5, reused verbatim by Task 6.
- CLI: `json_output` (not `json`) throughout; `deps`'s `--format` binds `fmt`.
- `SyncOutput` gains `feature_specs` once (Task 3); Task 8 references it in the changelog only.
- `settings.features_dir` is the single cache; no task adds another dir or manifest.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-03-dvt-dependency-awareness-v2.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
