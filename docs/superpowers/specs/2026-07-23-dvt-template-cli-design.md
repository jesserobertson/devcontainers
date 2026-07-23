# dvt (devtemplate) CLI Design

**Date:** 2026-07-23
**Status:** Approved

## Overview

A Python CLI, `dvt` (package name `devtemplate`), that gives `dev`-style named templates on
top of [DevPod](https://devpod.sh), targeting this repo's `templates/` (see
`docs/superpowers/specs/2026-07-23-cli-first-templates-design.md`) as its template source.

**Context on why this looks the way it does:** the original goal was
[`squirrelsoft-dev/dev`](https://github.com/squirrelsoft-dev/dev)'s UX — named global
templates, `dev new --template x`, `dev config add features <ref>` to layer more features
onto an existing project. `dev` doesn't build natively on Windows (Unix-only APIs in its
runtime backends) and is early-stage, so this project reimplements the parts of that UX that
matter — layered named templates and safe feature layering — in Python/Typer on top of
DevPod, which is already installed, already native on Windows, and already this repo's
documented CLI-first tool (see the CLI-first templates spec above). It deliberately reuses
`dev`'s own field-typed devcontainer.json merge algorithm (`src/devcontainer/merge.rs`)
rather than inventing a new one — that algorithm is well-tested and directly solves the
"layer a feature onto an existing project" problem this tool exists for.

## Location and packaging

New self-contained subdirectory `dvt/` in this repo (sibling to `templates/`, `features/`,
`host-services/`) — not nested under `features/cli/`, to avoid confusion with the
devcontainer *feature* of a similar name. Self-contained: own `pyproject.toml`, own
`tests/`, no imports reaching into repo root. This keeps it extractable to its own repo later
(subtree split) at zero cost if it outgrows the monorepo, but there's no forcing function
(no separate release cadence or other consumers yet) to justify a separate repo today —
especially since templates are fetched from GitHub at sync time, not read off local disk, so
there's no filesystem coupling to this repo's checkout either way.

```
dvt/
  pyproject.toml            # package "devtemplate", console-script entry point "dvt"
  src/devtemplate/
    cli.py                  # Typer app: top-level up/ssh/stop/delete + template/project sub-apps
    commands/
      template.py           # dvt template list/show/sync
      project.py             # dvt project init/add-feature
    config.py                 # pydantic-settings: XDG paths (via platformdirs), GitHub repo/branch
    github.py                  # fetch templates/ contents from GitHub (API listing + raw downloads)
    merge.py                    # devcontainer.json layering algorithm (ported from dev's merge.rs)
    models.py                    # Pydantic models for the devcontainer.json fields dvt reasons about
  tests/                          # pytest, mirrors tests/test_static.py conventions
```

### Distribution

`dvt/pyproject.toml` carries standard PEP 621 `[project]` + `[build-system]` (hatchling)
metadata, so `pipx install ./dvt` (or later a git/PyPI ref) works with zero pixi awareness —
plus a `[tool.pixi]` section, mirroring this repo's root `pyproject.toml`, purely for the
dev loop on `dvt` itself (`pixi install`, `pixi run pytest`, `pixi run ruff check`). pixi
sections are inert to pip/pipx; end users of `dvt` never see them. Primary install path:
`pipx install`.

**Stack:** Typer, Rich, Pydantic, pydantic-settings (matches `features/cli`'s
Typer/Rich/Pydantic convention) plus `platformdirs` (cross-platform XDG-correct path
resolution — `XDG_DATA_HOME` on Linux, correct equivalents on macOS/Windows, no hardcoded
`~/.dvt`) and `httpx` (GitHub API + raw content fetches).

## Configuration

`config.py`, pydantic-settings:

- **Template store path:** `platformdirs.user_data_dir("dvt")`.
- **Source repo:** `jesserobertson/devcontainers`, branch `main` — overridable via
  `DVT_GITHUB_REPO` / `DVT_GITHUB_BRANCH` env vars (mainly for testing against a fork).

## Template sync

`github.py` + `dvt template sync`: calls the GitHub Contents API to list `templates/`,
downloads each `templates/<name>/devcontainer.json` via `raw.githubusercontent.com` into the
XDG data dir. No `git` dependency, no local checkout of this repo required to use `dvt` from
anywhere.

Sync tracks which template names it manages in a small manifest file in the data dir, and
only overwrites those — any user-added custom template directories (dropped in by hand) are
left untouched. `dvt project init` auto-syncs once if the local cache is empty, and accepts
`--refresh` to force a re-sync first.

## Command surface

Typer-idiomatic noun-verb groups, plus flat top-level passthroughs for the commands typed
most often:

| Command | Behaviour |
|---|---|
| `dvt template list` | List cached templates (name, base image, feature) as a Rich table |
| `dvt template show <name>` | Print a cached template's `devcontainer.json` |
| `dvt template sync` | Refresh the cache from GitHub (see above) |
| `dvt project init --template <name> <path>` | Scaffold `<path>/.devcontainer/devcontainer.json` from a cached template; auto-syncs once if the cache is empty |
| `dvt project add-feature <name>` | Merge another feature's template into `./.devcontainer/devcontainer.json` (cwd-relative; see Merge algorithm) |
| `dvt up <name>` | `devpod up`, passthrough, forwarding extra args |
| `dvt ssh <name>` | `devpod ssh`, passthrough |
| `dvt stop <name>` | `devpod stop`, passthrough |
| `dvt delete <name>` | `devpod delete`, passthrough |

The lifecycle commands (`up`/`ssh`/`stop`/`delete`) are flat top-level commands, not nested
under a `dvt workspace` group — they don't manage local template state, they're the commands
typed most frequently, and DevPod itself already calls these "workspace" operations at the
`devpod` layer, so nesting them again under `dvt workspace` would just add a word without
adding clarity.

## Merge algorithm

`merge.py`, ported directly from `dev`'s `src/devcontainer/merge.rs` — a field-typed merge,
not a generic deep-merge, and not a refuse-on-conflict scheme:

- **Scalar fields** (`name`, `image`, `remoteUser`, `waitFor`, `shutdownAction`): the overlay
  (the feature template being added) overrides the base (the project's existing config).
- **`features`**: union by key.
- **`mounts`, `forwardPorts`**: concatenate, deduplicating entries present in both.
- **`runArgs`**: concatenate *without* deduplication — repeated flags (e.g. multiple
  `--env-file`) are legitimate and order is semantically meaningful.
- **`remoteEnv`, `containerEnv`**: map merge (overlay keys override base keys of the same
  name).
- **Lifecycle fields** (`postCreateCommand`, `postStartCommand`, `postAttachCommand`,
  `onCreateCommand`, `updateContentCommand`, `initializeCommand`): union only if both sides
  use the named-command-object form (`{"name": "cmd", ...}`); if either side is a plain
  string/array, the overlay's value replaces the base's outright.
- **Anything else unrecognized**: overlay wins.

`add-feature` only touches keys the new feature's template actually declares — fields the
project already has that the template doesn't mention are left exactly as-is. This is
deterministic with no ambiguous "print a diff and refuse" step: given this repo's templates
are internally uniform (`remoteUser: "dev"`, `postCreateCommand: "pixi install"` everywhere),
the override case is a no-op in the common path, and only matters if a project has hand-
customized a field a newly-added feature also declares — the same trade-off `dev` itself
ships with.

## JSON handling

Plain `json` module only — no JSONC/comment-preserving parser. `devcontainer.json`
technically permits comments and trailing commas per the VS Code spec, but this repo's own
templates are plain JSON, and adding comment-preservation is real complexity for a case that
doesn't arise from this tool's own output. If `add-feature`'s target file fails strict
`json.loads`, refuse to write and print the feature's snippet for the user to paste in by
hand instead of risking silently dropping comments or corrupting formatting.

## Testing

`dvt/tests/` (pytest), mirroring this repo's `tests/test_static.py` conventions:

- Merge algorithm tested against fixture JSON pairs (base + overlay → expected result),
  covering each field-type rule above individually and in combination (e.g. adding `agent`
  to an existing `fastapi` project: features union, `runArgs`/`postStartCommand`/`waitFor`
  added).
- GitHub fetch (`github.py`) tested with `httpx`'s mock transport — no real network calls in
  the test suite.
- XDG paths overridden via env vars (`XDG_DATA_HOME` etc., or `platformdirs`' own test hooks)
  in tests, never touching the real user data dir.

Run with `pixi run pytest` from `dvt/`.

## Out of scope for v1

- Custom/user-authored template creation (no `dvt template add` wizard) — dropping a
  hand-written template directory into the XDG data dir works today since sync only touches
  names it recognizes as repo-sourced, but there's no guided command for it yet.
- JSONC/comment-preserving edits (see JSON handling above).
- Moving `dvt` to its own repo (see Location and packaging above) — revisit if it grows a
  release cadence or consumers independent of this repo.
