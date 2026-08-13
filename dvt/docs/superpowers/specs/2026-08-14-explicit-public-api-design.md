# Explicit public API via `__all__` for `devtemplate`

## Problem

`src/devtemplate` currently signals "this is an implementation detail" almost
entirely through underscore-prefixed function names, scattered across
individual files. A 2026-08-14 audit (`docs/superpowers/specs/` session
history) found this is genuinely excessive in three files — most visibly
`ssh_server.py`, whose `_handle_process` (157 lines) is the structural twin
of `pty/bridge.py`'s already-public `bridge_to_ssh_process`, with three
module constants (`_CHUNK`, drain timeout, `_CHANNEL_EVENTS`) duplicated
across the two files because the shared module the duplication implies was
never created.

Separately, and independently of file size: the underscore convention is
inconsistently the *only* signal of intent. Nothing declares "these are the
public entry points of this module" as a single, checkable fact — a reader
has to infer it from which names lack an underscore, and nothing catches
that declaration drifting from reality as the module evolves.

## Goals

- Every module in `src/devtemplate` declares its public surface explicitly
  via `__all__`, rather than via underscore-prefix naming.
- Every package (`pty/`, `commands/`, `schemas/`, and the two new packages
  below) re-exports its public surface through `__init__.py` with its own
  `__all__`, so callers import from the package
  (`from devtemplate.pty import spawn_pty_process`) rather than needing to
  know which submodule defines a given name.
- Where `__all__` can be backed by a real, CI-enforced check (package
  boundaries, via mypy), it is — not left as an unchecked convention.
- Fix the three files the audit flagged as genuinely too dense
  (`ssh_server.py`, `features.py`, `workspace.py`) by extracting their real
  sub-concerns into purpose-named modules, and resolve the `ssh_server.py`
  / `pty/bridge.py` constant duplication as part of that.

## Non-goals

- No renaming or restructuring of files the audit found fine as-is
  (`podman_machine.py`, `merge.py`, `runtime.py`, `cli.py`,
  `workspace_lookup.py`, `commands/feature.py`, `container.py`, and
  everything with zero or trivial private surface) — they get `__all__`
  added, nothing else.
- No attempt to make `__all__` a hard runtime barrier. It is not one in
  Python, and this design doesn't pretend otherwise (see "Enforcement" —
  the real teeth are narrower than "prevents access", and that's
  deliberate).
- No change to `devtemplate/__init__.py` (root package) beyond what's
  already there (`__version__`). `dvt` is a CLI (`project.scripts` entry
  point), not a library other projects import — there is no top-level
  public API to declare.
- No change to class-internal (`self._foo`) naming conventions. This is
  specifically about module-level function/class privacy, not instance
  attributes or methods.

## Naming convention

Drop underscore prefixes from **every** module-level name in
`src/devtemplate`, codebase-wide — including the ones staying in their
current file (e.g. `workspace.py`'s `_feature_id` → `feature_id`,
`container.py`'s `_encode_metadata` → `encode_metadata`). A name's
privacy is determined solely by whether it appears in its module's
`__all__`, not by how it's spelled.

**Collision check:** before renaming, verify no file already has both
`foo` and `_foo` defined (a rename would collide). None were found during
the audit, but the implementation must check per-file before renaming, not
assume.

**Test imports:** several tests currently import underscore-named
internals directly for white-box testing (`tests/test_ssh_server.py`
imports `_handle_process`/`_NoAuthServer`, `tests/test_features.py`
imports `_parse_feature_ref`). These imports get updated to the renamed,
no-underscore names, still importing directly from the defining submodule
(not through a package's re-export) — that's normal, expected white-box
test practice and is unaffected by the package-boundary enforcement below.

## Enforcement

`__all__` by itself enforces nothing — Python does not stop
`from devtemplate.sshd.session import handle_process` regardless of what
any `__all__` declares. This is expected, not a gap to close; the goal is
an unambiguous, checkable declaration of intent, not an access-control
system.

Two enforcement layers, both already available in this project, verified
directly against the installed toolchain (not assumed from documentation):

- **Ruff (`F822`, `F401` — already selected via this project's `F` rule
  set, no config change needed):** `F822` fails if `__all__` names
  something that doesn't exist in the module. `F401` fails if a package's
  `__init__.py` imports a submodule name for re-export but doesn't list it
  in `__all__` (or alias it `as name`) — so a forgotten re-export shows up
  as an unused-import lint failure today, not a silent gap.
- **mypy (`implicit_reexport = false`, new addition to `[tool.mypy]` in
  `pyproject.toml`):** verified empirically against this project's
  installed mypy 2.3.0 — with this set, if package `P`'s `__init__.py`
  imports `name` from submodule `P.sub` without listing it in `P`'s
  `__all__`, then `from P import name` elsewhere fails mypy with
  `error: Module "P" does not explicitly export attribute "name"`. This is
  part of `pixi run -e dev quality check`, which already gates this
  codebase — so an undeclared cross-package import becomes a real CI
  failure, not a style nit. Default in this project's mypy is `true`
  (lenient); this flips it for the whole codebase.

This enforcement is real but narrow: it governs the *package-boundary*
case (does `__init__.py` honestly declare what it re-exports). It does not
— and cannot — stop a direct import of a flat module's own definitions
(`from devtemplate.container import encode_metadata` works regardless of
`container.py`'s `__all__`, since nothing is being re-exported through an
intermediary there). Flat modules' `__all__` is documentary, backed only
by `F822`'s existence-check; package `__init__.py`'s `__all__` is
documentary *and* mypy-enforced. Both are worth doing; only the second one
has real teeth, and the spec is explicit about which is which so nobody
mistakes a flat module's `__all__` for a guarantee it isn't.

## Package and module changes

### New package: `devtemplate/sshd/` (replaces `ssh_server.py`)

Highest-value split: removes the 157-line `_handle_process`, resolves the
constant duplication with `pty/bridge.py`, and makes the pty and non-pty
session-handling paths structurally symmetric (one is already a public
module; this makes both).

```
devtemplate/sshd/
  __init__.py   # re-exports run_stdio_server; __all__ = ["run_stdio_server"]
  server.py     # run_stdio_server, NoAuthServer, the process_factory/
                #   exit_codes wiring — the socketpair + asyncssh server setup
  session.py    # handle_process (was _handle_process): the pty/non-pty
                #   branch, and the non-pty plain-pipe pumps (was inline
                #   closures, becomes named functions in this module)
  stdio.py      # pump_stdio_to_socket (was _pump_stdio_to_socket) + its
                #   os.read/os.write rationale docstring, unchanged
```

- `session.py` imports the shared constants from `pty` (see below), not
  from a new `sshd`-owned module — `sshd` already depends on `pty` (it
  calls `bridge_to_ssh_process`), and this keeps that dependency
  one-directional rather than introducing a cycle-shaped pair of packages
  depending on each other.
- External API: `ssh.py`'s one import
  (`from devtemplate.ssh_server import run_stdio_server`) becomes
  `from devtemplate.sshd import run_stdio_server` — same shape, new
  package name.
- `tests/test_ssh_server.py` renames to `tests/test_sshd.py` (or splits
  along `server.py`/`session.py`/`stdio.py` lines if that reads better at
  implementation time — a plan-level decision, not a spec-level one),
  updating its imports to the no-underscore names, still importing
  directly from `devtemplate.sshd.session` etc. (white-box testing, per
  the Naming convention section above).

### `pty/` gains the shared constants and a populated `__init__.py`

- The three constants `sshd/session.py` currently duplicates from
  `ssh_server.py` (`CHUNK`, drain timeout, `CHANNEL_EVENTS`) move to live
  in `pty` — either added to `pty/bridge.py`'s existing `__all__` or a new
  `pty/constants.py`, implementation's call based on which reads more
  naturally once the code is in front of them. `sshd/session.py` imports
  them from there.
- `pty/__init__.py` (currently empty) becomes:
  `__all__ = ["spawn_pty_process", "bridge_to_ssh_process"]` plus the
  corresponding imports — the package's two actual entry points, matching
  what `ssh_server.py`/`sshd/session.py` already imports today.
- `PtyProcess` (the `Protocol` in `spawn.py`) is a type, not a runtime
  entry point, consumed only under `TYPE_CHECKING` by `bridge.py` today —
  it does not need to be in `pty/__init__.py`'s `__all__` unless a future
  consumer needs it re-exported; leave it as an internal type for now.

### `commands/` and `schemas/` gain populated `__init__.py`s

Same treatment for consistency: `__init__.py` imports and `__all__`-lists
each package's actual public surface (the Typer command objects for
`commands/`; whatever `schemas/` currently exposes by dotted-path import
elsewhere). The exact list is mechanical — determined by grepping current
call sites into each package — not a design decision this spec needs to
pre-make.

### New package: `devtemplate/features/` (replaces `features.py`)

Cheapest, clearest split — 6 of `features.py`'s 7 module-level functions
are a self-contained OCI Distribution client with exactly one caller
today.

```
devtemplate/features/
  __init__.py   # re-exports pull_feature; __all__ = ["pull_feature"]
  pull.py       # pull_feature — cache-dir policy, sha256 keying,
                #   orchestration; __all__ = ["pull_feature"]
  oci.py        # parse_feature_ref, parse_www_authenticate, get_token,
                #   fetch_manifest, first_layer_digest,
                #   fetch_and_extract_layer (all renamed, no underscore)
                #   + the two module constants, also renamed
                #   __all__ lists all six — genuinely reusable, not just
                #   de-underscored for the sake of it
```

- `workspace/up.py`'s one import
  (`from devtemplate.features import pull_feature`) is unchanged in
  spirit — it already imports at the package level, so this split doesn't
  even require touching that import statement, only where `pull_feature`
  is defined.
- `tests/test_features.py` updates its `_parse_feature_ref` import to the
  renamed, no-underscore name at its new location
  (`devtemplate.features.oci`).
- Not recommended (out of scope): further splitting `oci.py` into
  per-concern files (`ref.py`/`auth.py`/`manifest.py`/`blob.py`) — 166
  lines total doesn't support four more modules; that would be exactly the
  churn this design is trying to avoid.

### New package: `devtemplate/workspace/` (replaces `workspace.py`,
`workspace_existing.py`'s planned content, and the already-existing
`workspace_lookup.py`)

Revised from an earlier draft of this spec, which had these as three
sibling flat files distinguished only by a shared filename prefix. That
undersells what they actually are: one cohesive concern (orchestrating
`dvt up`) with three internal parts. A package makes that visible in the
directory structure itself, not just in filenames — consistent with how
`sshd/`, `features/`, and `pty/` already work in this design.

```
devtemplate/workspace/
  __init__.py   # re-exports up_workspace (+ resolve_for_up/
                #   resolve_existing if anything outside this package
                #   imports them today - confirm by grepping call sites
                #   at implementation time); __all__ lists those
  up.py         # up_workspace (the one __all__ export at this level) +
                #   feature_id, image_tag, load_config,
                #   refresh_ssh_config — renamed, no underscore, but NOT
                #   in this file's own __all__ (internal to the package,
                #   just not signaled by spelling anymore)
  existing.py   # resume_existing, config_drift_error,
                #   folder_mismatch_error, rebuild_teardown, plus the
                #   ~35 lines of lenient-vs-strict folder-confirmation
                #   branching currently inline in up_workspace — this
                #   file's __all__ lists whichever of these up.py
                #   actually calls (likely all four)
  lookup.py     # was workspace_lookup.py, content unchanged by this
                #   split - names_by_folder, multiple_matches_error
                #   (renamed, no underscore, internal) + resolve_for_up,
                #   resolve_existing (this file's __all__)
```

- `up.py` keeps the linear pull → build → run → lifecycle → ssh-config
  pipeline; the existing-container decision becomes a call (or small
  number of calls) into `existing.py`, not inline branching.
- Cross-submodule imports within this package use the same full
  absolute-dotted-path style the rest of the codebase already uses
  everywhere (`from devtemplate.workspace.existing import
  resume_existing`, not a relative `from .existing import ...`) — this
  codebase has no relative imports today, and this package doesn't
  introduce the first one.
- `container.py` is explicitly NOT part of this package — it's a
  different concern (translating devcontainer.json into docker-py
  run/label/lookup calls), the audit found it fine as-is, and nothing
  about "workspace orchestration" implies "container runtime
  interaction" needs to move too.
- `tests/test_workspace_lookup.py` (if that's its current name) keeps its
  test content unchanged, just updates its import to
  `devtemplate.workspace.lookup`.

### Flat modules gaining `__all__` with no structural change

Every remaining `.py` file under `src/devtemplate` — including ones with
zero current private helpers (`models.py`, `config.py`, `sidecar.py`,
`schema.py`, `github.py`, `cli_support.py`, `ssh.py`) and ones the audit
found fine as multi-helper single files (`podman_machine.py`, `merge.py`,
`runtime.py`, `cli.py`, `container.py`, `commands/feature.py`,
`commands/info.py`, `commands/init.py`, `build.py`, `store.py`) — gets
an explicit `__all__` listing its current
public names, and any underscore-prefixed helper renamed per the Naming
convention section. No file moves, no new modules. This is mechanical,
same-shape work across ~15-17 files and is expected to be batched into one
or a small number of task dispatches per file, not one dispatch each.

## Testing implications

- Every test file that imports a renamed underscore name updates its
  import statement to match (a mechanical, per-file change alongside the
  rename itself, not a separate task).
- `tests/test_ssh_server.py` → `tests/test_sshd.py` (or split, see above)
  needs its imports updated for the `sshd/` package, and any test that
  constructs the non-pty pipe path directly (rather than through
  `run_stdio_server`) needs to import from `devtemplate.sshd.session`
  instead of the old flat module.
- `tests/test_features.py` needs its import updated for the `features/`
  package.
- `tests/test_workspace*.py` (the `up_workspace` tests and the existing
  `workspace_lookup.py` tests) need their imports updated for the new
  `workspace/` package's submodule paths; test content/assertions are
  unchanged for the lookup tests since `lookup.py`'s content doesn't
  change, only its location.
- No test's actual *behavior* assertions change — this is a pure
  reorganization plus a rename; the SSH PTY feature's own extensive test
  suite (both in `sshd`'s territory and `pty`'s) must keep passing
  byte-for-byte, same as every constraint carried through that feature's
  own implementation.

## Risks

- **Import-path churn is the main risk.** Every call site of a renamed or
  moved function needs updating in the same change as the rename — this
  is mechanical but has a lot of surface area (grep-and-replace across
  `src/` and `tests/`), and a missed call site is a real `ImportError`,
  not a silent bug. Each task in the eventual plan should include a "grep
  for the old name across the whole tree, confirm zero remaining
  references" verification step.
- **`implicit_reexport = false` is a global mypy setting** — turning it on
  affects every package in the codebase at once, including `commands/`
  and `schemas/` even though this spec's package-level changes there are
  described only loosely (mechanical, not designed in detail here). If
  either currently has an implicit re-export mypy would newly flag, that
  surfaces as a `quality check` failure during implementation and needs
  fixing as part of turning the flag on, not deferred.
- **`pty`'s package name already shadows the stdlib `pty` module**
  (documented and handled correctly in `posix.py` today). Adding more
  content to `pty/__init__.py` doesn't change this, but is worth keeping
  in mind if `pty/__init__.py`'s own imports ever need the stdlib `pty`
  module directly (they don't, today).

## Testing strategy for the implementation itself

Each task (per-file `__all__` addition, or one of the three structural
splits) follows the same shape used successfully for the SSH PTY feature:
implement, run the affected tests, run the full suite
(`pixi run test unit`), run the quality gate
(`pixi run -e dev quality check` — now including the new
`implicit_reexport = false` check), commit, task-scoped review. A final
whole-branch review happens once all tasks are done, matching the process
that caught real bugs in the SSH PTY feature's own final review — this
kind of mechanical, wide-blast-radius change is exactly the profile where
a single missed import or collision could slip past individual task
reviews.
