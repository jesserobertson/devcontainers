# dvt dependency awareness (v2 — revised against `feature_graph`)

**Date:** 2026-09-03
**Status:** Draft
**Supersedes:** `docs/superpowers/specs/2026-09-02-dvt-dependency-awareness-design.md`

## Why a v2

The original v1 spec was written before `dvt` had any build-time dependency
resolution. It proposed a second, GitHub-raw spec cache (`feature_specs.py` +
new `github.py` fetchers) and a standalone resolver (`deps.py`) purely so
`dvt feature list` / `show` / `deps` could *display* the graph.

Since then, `dvt/src/devtemplate/feature_graph.py` shipped (commit `6ed7268`).
It already:

- reads the ordering-relevant slice of a Feature's own
  `devcontainer-feature.json` — `read_feature_spec(extracted_dir) -> FeatureSpec`
  where `FeatureSpec` is `(id, depends_on, installs_after, container_env)`, with
  every ref run through `normalise_ref` (tagless → `:latest`);
- resolves the transitive `dependsOn` closure in a deterministic install order —
  `resolve_feature_graph(explicit: dict[ref, options], pull) -> Result[list[ResolvedFeature]]`,
  a stable topological sort over the `dependsOn` + `installsAfter` edges with a
  total-order tie-break, cycle detection via the module's `_find_cycle` helper;
- works for **any** OCI registry (it pulls the artifact via `pull_feature`), not
  just this repo's GitHub path.

So the visualisation can read the *same data the builder uses*, and v2 is mostly
v1 with work removed:

| v1 element | v2 |
|---|---|
| `github.py` `list_feature_names` / `fetch_feature_spec` | **dropped** |
| `feature_specs.py` (cache + manifest + trimmed records) | **dropped** — reuse `pull_feature`'s existing `settings.features_dir` cache |
| standalone `deps.py` (`FeatureRecord`, `Resolution`, `resolve`) | **dropped** — one pure function added to `feature_graph.py` |
| `dvt sync` fetches `devcontainer-feature.json` from raw.githubusercontent.com | `dvt sync` **pre-pulls the OCI artifacts** into the build cache |
| GitHub-only (jesserobertson/devcontainers) | any registry |
| sidecar `pulls_in` record on `feature add` | **dropped** (derivable) |

The four user-facing surfaces are unchanged in intent: a "Pulls in" column in
`dvt feature list`, a dependency tree in `dvt feature show`, a new
`dvt feature deps` command, and an "also pulling in" line at `dvt feature add`.

## Non-goals

- **No second spec source.** The only place a Feature's `dependsOn` /
  `installsAfter` is read is `feature_graph.read_feature_spec`, from a pulled OCI
  artifact under `settings.features_dir`. `dvt feature list` / `show` / `deps`
  read that cache; they never fetch.
- **No `dvt feature add` rewrite of `devcontainer.json`.** The builder
  (`feature_graph.resolve_feature_graph`, wired into `workspace/up.py`) already
  applies `dependsOn` at image-build time. `add` only *reports* what will be
  pulled in; it does not inject implied refs into the project's `features` map,
  and no longer records them in the sidecar.
- **No `${containerEnv:VAR}` interpolation, no `overrideFeatureInstallOrder`
  visualisation** — `feature_graph` doesn't implement either (documented in its
  module docstring), and neither surfaces in the UI.
- **No `--format` for `list` / `show`.** Only `dvt feature deps` gets
  `tree|dot|mermaid`.
- **`dvt sync` pre-pull failures are non-fatal.** A feature whose artifact can't
  be pulled (registry down, private, moved) produces a warning; `sync` still
  succeeds, the graph for that feature degrades to `—`, and a real `dvt up`
  still pulls it on demand.

## `dvt sync` — pre-populate the feature cache

`dvt/src/devtemplate/cli.py`'s `sync` command today: `clear_pulled_features(settings.features_dir)`,
then `sync_templates`, then `sync_images`, then reports counts.

Add a fourth step after `sync_images`, in the same `httpx.Client` block:

1. **Collect the known feature refs.** For every cached template
   (`store.list_cached_templates` → `store.load_cached_template`), take the keys
   of its `features` map. Also read
   `images/base-*/.devcontainer/devcontainer.json` from the **local source
   checkout** if one is reachable (`images.find_repo_root(Path.cwd())` already
   exists for `dvt image set/unset`) to pick up the three plumbing refs
   (`homebrew`, `shell-kit`, `pixi`) that no template lists directly. If no
   checkout is reachable, skip that part — the plumbing features are still
   reached transitively in step 2.
2. **Pull each, following `dependsOn`.** Reuse the resolver:
   `feature_graph.resolve_feature_graph({ref: {} for ref in collected}, lambda r: pull_feature(client, r, settings.features_dir).unwrap())`.
   The resolver pulls every explicit ref plus every transitive `dependsOn`
   target into `settings.features_dir` as a side effect; its ordered return
   value is discarded here. Wrap the call so a pull failure is caught per-ref:
   iterate the collected refs and call the resolver one ref at a time (each call
   still transitively pulls that ref's deps), collecting `Ok`/`Err` per ref, so
   one unreachable feature doesn't abort the rest.
3. **Report.** The synced-feature-spec ids are
   `feature_graph.load_cached_specs(settings).keys()` after the pulls. Add
   `feature_specs: list[str]` to the human summary ("Synced N feature specs:
   …") and to `SyncOutput`.

### `cli_output_schemas.py`

```python
class SyncOutput(BaseModel):
    ok: Literal[True]
    features: list[str]
    images: list[str]
    feature_specs: list[str]
```

### Cost

`dvt sync` now does ~15 OCI pulls (each: token + manifest + one layer, a few
hundred KB). `pull_feature` caches by `sha256(ref)` and `clear_pulled_features`
wiped the cache at the top of `sync`, so every pull is real on a `sync` but
free afterwards. Acceptable — `sync` is the explicit "go get what's current"
command. Parallelising the pulls is a possible later optimisation, not in scope.

## `feature_graph.py` additions

Two pure, network-free additions (they operate on already-read `FeatureSpec`s):

```python
@dataclass(frozen=True)
class GraphNode:
    id: str
    pulls_in: tuple[str, ...]        # transitive dependsOn closure as ids, sorted,
                                     # deduped; a ref outside `specs` kept as its
                                     # bare normalised ref
    installs_after: tuple[str, ...]  # this feature's own installsAfter, mapped to
                                     # ids where known, bare ref otherwise; NOT
                                     # transitive

def describe_graph(
    specs: Mapping[str, FeatureSpec]
) -> Result[dict[str, GraphNode], Exception]:
    """One GraphNode per spec. `pulls_in` is the DFS closure over `depends_on`
    edges among `specs`; `installs_after` is verbatim (non-transitive). A cycle
    in the `depends_on` edges returns Err (reuse `_find_cycle`)."""

def to_dot(nodes: Iterable[GraphNode]) -> str: ...      # digraph, A -> B = "A pulls in B"
def to_mermaid(nodes: Iterable[GraphNode]) -> str: ...  # graph TD, A --> B
```

- `describe_graph` keys and ref-comparison use the same normalised refs
  `FeatureSpec` already stores, and maps a ref to a short id via the existing
  convention (`ref.rsplit("/", 1)[-1].split(":")[0]` — the same derivation
  `workspace/up.py:feature_id` uses; factor it into `feature_graph` as
  `ref_to_id` and have `up.py` import it).
- `to_dot` / `to_mermaid` produce deterministic output (sorted edges).
- `__all__` gains `GraphNode`, `describe_graph`, `to_dot`, `to_mermaid`,
  `ref_to_id`, `load_cached_specs`.

## The cache loader

```python
def load_cached_specs(settings: Settings) -> dict[str, FeatureSpec]:
    """Every readable devcontainer-feature.json under settings.features_dir,
    keyed by FeatureSpec.id. {} when the dir is absent. A dir whose
    read_feature_spec raises is skipped (logged at debug), not fatal."""
```

`pull_feature` extracts each artifact to `settings.features_dir / sha256(ref)`;
`load_cached_specs` globs those subdirs, calls `read_feature_spec` on each, and
dedupes by `id` (last wins — in practice the ids are unique). Lives in
`feature_graph.py` alongside `read_feature_spec`.

## Command changes — `dvt/src/devtemplate/commands/feature.py`

All four surfaces call `load_cached_specs(settings)` once, then
`describe_graph(specs)`; a resolve `Err` (cycle) or an empty cache degrades
gracefully — `list` / `add` never hard-fail.

### `list_features`

- Table gains a **"Pulls in"** column between `Description` and `Base Image`:
  `", ".join(nodes[name].pulls_in)` or `"—"` when empty / `name` not in `nodes`
  / cache cold.
- `--json` rows gain `"pulls_in": list[str]` (`[]` when unavailable).
- When `load_cached_specs` returns `{}`: every row shows `"—"`, no error, and a
  single dim stderr line `run 'dvt sync' for dependency info` (non-JSON only).

### `show_feature`

- Non-JSON: after the existing overlay JSON, if `name in nodes`, print a
  `rich.tree.Tree` rooted at `name` whose children are the `dependsOn` subtree
  recursively (built from `specs`, not just `nodes[name].pulls_in`, so the tree
  shows structure not just the flat closure); annotate a node that has
  `installs_after` with ` [dim](after: …)[/dim]`.
- `--json`: emit `{**overlay, "resolved_depends_on": list(nodes[name].pulls_in)}`
  when `name in nodes`, else the raw overlay unchanged (preserves the current
  "success output is always the raw overlay" contract for the uncached case).

### `deps` — new command

```
dvt feature deps [NAME] [--format tree|dot|mermaid] [--json]
```
Also registered as `dvt feature tree` (alias).

- `NAME` given: fuzzy-resolved via the existing `resolve_or_confirm` pattern
  (`show` uses it) against `load_cached_specs(...).keys()`. `NAME` omitted: the
  whole fleet.
- `--format tree` (default): a Rich tree per feature that has any `depends_on`
  (reuse `show`'s tree helper).
- `--format dot` / `mermaid`: `feature_graph.to_dot` / `to_mermaid` over the
  selected `GraphNode`s, to stdout.
- `--json`: `{ "<id>": {"pulls_in": [...], "installs_after": [...]}, ... }`.
- Empty cache → exit 0, `No feature dependency data. Run 'dvt sync' first.` on
  stderr (non-JSON) / `{}` (JSON).
- If the repo registers per-command output schemas keyed by name in
  `cli_output_schemas.py`, add an entry for `"feature deps"` following the
  existing convention.

### `add`

- In `add` (not `add_one`), after `resolved` is known: `nodes = describe_graph(load_cached_specs(settings))`;
  if `resolved in nodes` and `nodes[resolved].pulls_in` is non-empty and not
  `json_output`, print `also pulling in: <a, b> (via dependsOn)` to `console`.
- **No sidecar change.** `add_one` still appends `{"name": name, "overlay": overlay}`;
  drop the v1 `"pulls_in"` key. `remove_one` unchanged.
- `devcontainer.json` is not modified for implied features.

## Tests — `dvt/tests/`

- `test_feature_graph.py` (extend): `describe_graph` — direct edge, transitive
  closure, diamond dedup, `installs_after` excluded from `pulls_in`, cycle →
  `Err`, a ref outside `specs` kept as bare ref. `to_dot` / `to_mermaid` output
  shape and determinism. `ref_to_id`. `load_cached_specs` over a `tmp_path`
  fixture with two extracted-dir fixtures + one malformed (skipped).
- `test_sync` / `test_cli` (extend): mocked `pull_feature` — `sync` pre-pulls the
  template feature refs + transitive `dependsOn`; `SyncOutput` has
  `feature_specs`; one ref's pull raising leaves the rest synced with a warning.
- `test_feature_command.py` (extend): `list` "Pulls in" column present +
  correct; `—` and the stderr hint when cache cold; `--json` `pulls_in`. `show`
  tree rendered; `--json` `resolved_depends_on`; uncached name still prints the
  raw overlay. `add` prints `also pulling in: …` for a `dependsOn` feature;
  sidecar entry has no `pulls_in` key; `devcontainer.json` `features` unchanged.
- `test_feature_deps_command.py` (new): single + fleet; `--format dot` /
  `mermaid` / `--json` shapes; unknown name → error; empty cache → `{}` / the
  stderr line.
- Any existing `dvt` test asserting the old 3-column `feature list` table
  header/row shape updates to 4 columns.

## Docs

- `dvt/CHANGELOG.md`: new `## [0.5.0]` — `dvt sync` now also pre-pulls each
  known feature's artifact; `dvt feature list` gains a "Pulls in" column;
  `dvt feature show` renders the dependency tree; new `dvt feature deps`
  (`tree|dot|mermaid|json`); `dvt feature add` reports `dependsOn` pull-ins.
- `dvt/pyproject.toml`: `version = "0.5.0"`.
- `dvt/README.md` + `dvt/docs/content/quickstart.md`: `dvt feature deps` in the
  command list; the "Pulls in" column under `dvt feature list`; a note to run
  `dvt sync` once after upgrading so the column populates.
- `dvt/docs/content/concepts.md`: the "`dvt` does not implement … Feature
  dependency ordering" bullet was already removed (commit `9867caf`); no further
  change.

## Rollout & versioning

One `dvt` minor bump to `0.5.0`, one `dvt-v0.5.0` tag post-merge (triggers
`publish-dvt.yml` TestPyPI + `release-dvt.yml`), real-PyPI stays manual
`workflow_dispatch` — the repo's existing flow.

## Backward compatibility

- A `dvt` that hasn't re-synced since upgrading has an unpopulated
  `settings.features_dir` for the new purpose (it's cleared each `sync` and was
  previously only populated lazily by `dvt up`): the "Pulls in" column shows
  `—`, `deps`/`show` print the run-`dvt sync` hint, `list`/`add` never fail.
  First `dvt sync` fixes it.
- `dvt up` is unaffected — `resolve_feature_graph`'s own on-demand `pull_feature`
  calls still populate the cache during a build regardless of `sync`.
- `SyncOutput` gaining a field is additive; existing `--json` consumers that
  key `features` / `images` are unaffected.

## Risks

- **`dvt sync` slower / noisier.** ~15 sequential OCI pulls per `sync`. If a
  registry is flaky the per-ref warnings could be noisy; the resolver's own
  `on_err` retry (via `pull_feature` → `oci.py`) absorbs transients. Accepted;
  parallelisation is a later optimisation.
- **Plumbing-ref discovery depends on a source checkout.** `homebrew` /
  `shell-kit` / `pixi` have no template. From a checkout, `dvt sync` reads
  `images/base-*/.devcontainer/` and pulls all three directly (and `shell-kit`'s
  `dependsOn: homebrew` pulls `homebrew` again — a cache hit). Without a
  checkout, transitive `dependsOn` still reaches `pixi` (every Python toolchain
  `dependsOn`s it) and `homebrew` (`rust-devtools` / `cpp-devtools`
  `dependsOn` it), but **not** `shell-kit` — nothing with a template
  `dependsOn`s `shell-kit`, and `pixi` only `installsAfter` it (which never
  pulls). So `dvt feature deps shell-kit` shows `—` for a checkout-less user
  until a `dvt up` on a `base-ubuntu` project caches it. Low impact; documented.
- **`ref_to_id` collisions.** Two features from different registries with the
  same trailing path segment would collide in `load_cached_specs` /
  `describe_graph`. This repo's features are all under one namespace with
  unique ids, and `@devcontainers/cli` has the same limitation. Accepted.
