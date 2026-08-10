# CLI redesign: `dvt init` + `dvt feature` CRUD

## Problem

The current CLI has two subgroups, `dvt template` (registry ops: `sync`/`list`/`show`) and
`dvt project` (`init`/`add-feature`), plus a third, unrelated meaning of "feature" — the real
OCI devcontainer Features pulled at `dvt up` time (`features.py::pull_feature`). The `project`
subgroup hides what's actually a small set of operations behind a name that doesn't explain
itself, there's no `remove` counterpart to `add-feature`, and there's no way to see what
features are available with a human-readable description of what each one does.

Inspecting the actual template files confirms `template` isn't a distinct concept: every
`templates/<name>/devcontainer.json` in `jesserobertson/devcontainers` is a fixed base image
(`base-ubuntu` or `base-cuda`), exactly one OCI Feature ref matching the template's own name,
and boilerplate dvt can generate itself (`workspaceFolder`/`workspaceMount`, a
`<name>-pixi-cache` mount, `postCreateCommand: pixi install`, `remoteUser: dev`) — plus
occasionally a couple of feature-specific extras (e.g. `agent`'s firewall `runArgs`/
`postStartCommand`/`waitFor`). "Template" is just a Feature overlay wearing a scaffolding hat.

## Goals

- Flat CLI: `dvt init`, `dvt feature {list,show,sync,add,remove}`. No `project`/`template`
  subgroups, no aliases kept — this is a breaking change, shipped as a minor version bump.
- `dvt feature list` shows every feature dvt knows about with a description, in a terminal
  table and as `--json` for scripting.
- `dvt feature remove` becomes possible, and is safe to run against a devcontainer.json that's
  been hand-edited since — it must not clobber fields a feature `add` didn't touch.
- Real semantic versioning: one source of truth for the version number, a `dvt --version`
  flag, and a maintained `CHANGELOG.md` — today's version string is duplicated by hand in two
  files and nothing surfaces it to the CLI.

## Non-goals

- No change to `dvt up`/`ssh`/`stop`/`delete`.
- No change to how real OCI Features are pulled/baked at `up` time (`features.py`,
  `workspace.py`) — this is purely about the registry/scaffolding layer.
- No backward-compat shim for `dvt project`/`dvt template` — removed outright.

## CLI surface

```
dvt init <path> [--image REF]        # scaffold boilerplate devcontainer.json, no features
dvt feature list [--json]            # registry: available features + descriptions
dvt feature show <name>              # print a cached feature's overlay JSON
dvt feature sync                     # refresh the cached registry from GitHub
dvt feature add <name>               # layer a feature onto ./.devcontainer/devcontainer.json
dvt feature remove <name>            # un-layer it
```

`app.add_typer(project.app, name="project")` and `app.add_typer(template.app, name="template")`
are removed from `cli.py`; `commands/project.py` and `commands/template.py` are replaced by a
single `commands/feature.py`, plus `init` moving to a top-level command in `cli.py` (or a small
`commands/init.py` — implementation's call).

### `dvt init`

Writes just the generic boilerplate every template currently duplicates:

- `image`: defaults to `ghcr.io/jesserobertson/base-ubuntu:latest`; overridable with
  `--image`. The default is visible in `--help` — the option's help text reads
  `"Base image (default: ghcr.io/jesserobertson/base-ubuntu:latest)"`.
- `workspaceFolder` / `workspaceMount`, `remoteUser: dev`, `postCreateCommand: pixi install`.
- `name`: the target directory's own name, same as today.
- No `features` key.

Still refuses (exit 1, nothing written) if `.devcontainer/devcontainer.json` already exists,
and still scaffolds a minimal `pixi.toml` when the project doesn't already manage its own
dependencies — both unchanged from today's `project init`. The `--refresh`/`--template` options
are dropped entirely: `init` no longer touches the feature registry.

### `dvt feature add <name>`

Same merge as today's `add-feature`: `merge_layer` from `merge.py`, `IDENTITY_FIELDS`
(`name`/`workspaceFolder`/`workspaceMount`) stripped from the incoming overlay first, schema
validation before write (refuses, file untouched, on any failure). One behavior addition: if
the local feature cache is empty, `add` auto-syncs first — `init` no longer does this (it never
touches the registry), so `add` is the first point that needs cached data, and requiring a
separate manual `dvt feature sync` before the first `add` would be a regression from today's
"it just works" `project init --template` flow.

After a successful write, `add` also appends `{"name": name, "overlay": overlay}` to the
tracking sidecar (see below) and writes it.

### `dvt feature remove <name>`

New. The constraint: `feature add` already promises "fields the project already has that the
new feature's overlay doesn't mention are left exactly as they are" (existing concepts.md).
`remove` must honor the same promise — it must not clobber fields the target file has that
`<name>`'s overlay never touched, including manual edits made after `add`. That rules out
"rebuild the whole file from a tracked feature list," which would silently discard any
hand-editing.

**Tracking sidecar**: `.devcontainer/dvt-features.json`, written alongside
`devcontainer.json`:

```json
{
  "init": {"image": "...", "workspaceFolder": "...", "workspaceMount": "...",
           "remoteUser": "dev", "postCreateCommand": "pixi install"},
  "applied": [
    {"name": "fastapi", "overlay": {"image": "...", "features": {...}, "mounts": [...], ...}}
  ]
}
```

`init` writes the `init` block once, `add` appends to `applied`, `remove` removes from it.
Each `applied` entry's `overlay` is the exact dict `add` merged in, frozen at that moment —
not re-fetched from the cache on `remove`, so a later `feature sync` upstream change can't
silently alter what `remove` reverses.

**`remove <name>` algorithm**:

1. Load the sidecar. If `<name>` isn't in `applied`, refuse (exit 1, nothing written) with an
   error explaining the feature isn't tracked, so it can't be safely removed — covers
   devcontainer.json files predating this sidecar, or where the feature was added by hand.
2. `touched_keys` = the set of top-level keys `<name>`'s recorded overlay contains.
3. For each key in `touched_keys`, recompute its value by replaying the *existing* per-field
   merge helpers in `merge.py` (`_merge_lifecycle_command`, `_merge_feature_map`,
   `_merge_array_dedup`, `_merge_array_concat`, `_merge_map`, and scalar-overwrite for
   everything else) over `[init_block, *remaining applied overlays in order]`, restricted to
   that key. This is `merge_layer`'s own logic, just scoped to `touched_keys` instead of every
   key in each layer — `merge.py` gets a new `merge_layer_keys(layers, keys)` (or equivalent)
   that both `merge_layer` and `remove` call, rather than duplicating the per-field dispatch.
4. Splice the recomputed values into the *current* devcontainer.json (only `touched_keys` —
   everything else, including hand edits, is left byte-for-byte as it was). A key with no
   remaining layer setting it is deleted from the file outright, not left as `{}`/`[]`/`None`.
5. Validate the result against the schema before writing; refuse (file untouched) on failure,
   same as `add`.
6. Write the file, then rewrite the sidecar without `<name>`.

### `dvt feature list` / `dvt feature show`

- `dvt feature list`: Rich table — Name, Description, Base Image. `--json`: array of
  `{"name", "description", "image", "feature_ref"}` for every cached feature, printed as plain
  JSON to stdout (no Rich formatting) so it's pipeable.
- `dvt feature show <name>`: unchanged from today's `template show` — prints the cached
  feature's raw devcontainer.json (already valid JSON; no separate `--json` needed since the
  output already is JSON).
- `description` comes from a new `"description"` field on each `templates/<name>/devcontainer.json`
  in `jesserobertson/devcontainers` (out of scope for this repo's changes, but required — see
  below). Falls back to `""` for any template not yet updated, so an existing cache with old
  template files doesn't error.

## Out-of-repo change required

`jesserobertson/devcontainers`'s `templates/<name>/devcontainer.json` files each need a new
`"description"` field, e.g.:

```json
{
  "name": "fastapi",
  "description": "FastAPI service with pixi-managed Python and a pixi cache volume.",
  "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
  ...
}
```

This is an additional top-level field; nothing in dvt's schema validation path applies to the
source templates themselves (only to the *merged result* written into a project), so this is
safe to add. `load_cached_template`/`fetch_template` need no changes — they already round-trip
the whole dict.

## Error handling

- `feature add`/`feature remove`: unchanged refuse-and-leave-untouched behavior on invalid
  JSON, missing cache entry, or schema validation failure.
- `feature remove` additionally refuses on an untracked feature name (see above).
- `feature list`/`show`: unchanged — empty cache prints a hint to run `feature sync`.
- `init`: unchanged refuse-if-exists behavior; `--image` takes any string, validated only by
  the schema check the eventual `up`/build already performs (no new validation here — same
  as how `image` is handled today).

## Testing

- `tests/test_project_command.py` → `tests/test_init.py`: init boilerplate content
  (image default, `--image` override, help text mentioning the default), pixi.toml scaffolding
  (unchanged assertions), refuse-if-exists.
- `tests/test_template_command.py` → `tests/test_feature_command.py`: `list` (table + `--json`,
  including a feature missing `description`), `show`, `sync` (unchanged from today), `add`
  (unchanged merge assertions + sidecar-append), and new `remove` coverage:
  - removes a single applied feature, restores the file to its pre-add state for the touched
    keys.
  - a hand-edited field untouched by the feature survives `remove` unchanged.
  - two applied features touching the same key (e.g. two features both setting `image`) —
    removing the earlier one leaves the later one's value; removing the later one restores the
    earlier one's.
  - refuses to remove an untracked/unknown feature name, file unchanged.
- `tests/test_merge.py`: coverage for the new `merge_layer_keys`-style scoped replay helper.
- `tests/test_cli_help.py`: update for the new top-level command list (no more
  `project`/`template` subgroups); add a case for `dvt --version` printing `dvt <version>` and
  exiting 0 without requiring any settings/runtime to be available (it's an eager callback, so
  this must work even with no Docker/Podman present).
- A new test asserting `devtemplate.__version__` matches `pyproject.toml`'s `version` (guards
  against the two ever drifting again, now that one is derived from the other via installed
  package metadata — this test would have caught today's duplication).
- `docs/content/commands.md` and `docs/content/quickstart.md`: rewritten for the new surface.

## Versioning and changelog

Today, version is a hardcoded string duplicated in two places — `pyproject.toml`'s
`[project].version` and `src/devtemplate/__init__.py`'s `__version__` — with no changelog and
no `--version` flag. This is a breaking CLI change (no aliases retained), which is exactly the
kind of change SemVer exists to signal, so this piece fixes the gap rather than bumping a
number that means nothing yet:

- **Single source of truth**: `pyproject.toml`'s `version` stays the authoritative value.
  `src/devtemplate/__init__.py` stops hardcoding a duplicate literal and instead reads it at
  import time via `importlib.metadata.version("devtemplate")`, so the two can't drift out of
  sync again. (No move to `hatch-vcs`/git-tag-derived versioning — out of scope; that's a
  release-automation decision, not part of this CLI change, and there's no CI in this repo yet
  to hang it off of.)
- **`dvt --version`**: an eager callback on the top-level Typer `app` in `cli.py`
  (`@app.callback()` with a `--version` option using `is_eager=True`), printing
  `dvt <version>` and exiting before any subcommand logic runs — the standard Typer pattern.
  Sourced from the same `devtemplate.__version__`.
- **`CHANGELOG.md`** at the repo root, [Keep a Changelog](https://keepachangelog.com) format
  (`## [Unreleased]` / `## [x.y.z] - YYYY-MM-DD`, `### Added`/`### Changed`/`### Removed`
  subsections). Seeded with a `0.1.0` entry reconstructed from git history (initial release),
  plus this work's own `0.2.0` entry documenting the breaking CLI change (`project`/`template`
  subgroups removed, `feature` CRUD + `dvt init` added, `--version` added). Maintained by hand
  going forward — no automated changelog generation, nothing in this repo to drive it off yet.
- SemVer policy going forward (documented as a short note in `CHANGELOG.md`'s header, not a
  separate doc): breaking CLI/behavior changes bump minor while pre-1.0 (per SemVer's own
  "anything may change" allowance for `0.x`), patch releases are fixes only. Revisit switching
  breaking changes to major once the project reaches `1.0.0`.

`pyproject.toml`'s `version` bumps `0.1.0` → `0.2.0` as part of this work.
