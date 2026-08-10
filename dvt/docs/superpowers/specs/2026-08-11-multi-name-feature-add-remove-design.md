# Multi-name `dvt feature add`/`remove`

## Problem

`dvt feature add`/`remove` each take exactly one feature name today. Adding several features
to a project means running the command once per name (`dvt feature add py-devtools && dvt
feature add marimo`). That's the common case when scaffolding a new project — it's rarely just
one feature — so it should be one command: `dvt feature add py-devtools marimo`.

## Goals

- `dvt feature add`/`remove` each accept one or more names, applied/removed in the order given.
- If one name in the batch fails, everything before it stays applied (each add/remove is
  already atomic per name, exactly as today) and nothing after it is attempted — the command
  exits 1, having reported exactly what succeeded and what stopped it.
- A single name behaves byte-for-byte identically to today (a batch of one) — no existing
  single-name test should need to change.

## Non-goals

- No other command gains multi-name support. `dvt feature list`/`show`/`sync` and `dvt
  init`/`up`/`ssh`/`stop`/`delete` are unaffected — `list` already shows everything, `sync`
  always syncs everything, `show` is a single lookup, and `up`/`ssh` inherently operate on one
  workspace per invocation; `stop`/`delete`'s cwd-based inference (this session's other work)
  is scoped to "the one workspace tied to this folder," a different question from batching.
- No best-effort "try all, report a summary" mode — first failure stops the batch (see Goals).

## Design

`add`/`remove`'s per-name logic (everything each currently does for its single `name`) moves
into a private helper — `_add_one(name, settings, devcontainer_dir, target) ->
Result[None, Exception]` and `_remove_one(name, devcontainer_dir, target) ->
Result[None, Exception]` — matching the `logerr` `Result` convention already used throughout
this codebase (`load_sidecar`, `write_sidecar`, `resolve_for_up`, `sync_templates`, ...). Every
failure path that currently does `console.print(...)` + `raise typer.Exit(code=1)` instead
constructs an `Exception` carrying the same message text and returns `Err(...)`; the one
success path still prints its own `"Added feature '<name>' to <target>."` /
`"Removed feature '<name>' from <target>."` line and returns `Ok(None)`.

The public commands change their argument from `name: str` to `names: list[str]` (Typer's
standard variadic-positional pattern — collects every trailing CLI argument into a list) and
loop:

```python
for name in names:
    unwrap_or_exit(_add_one(name, settings, devcontainer_dir, target), console)
```

`unwrap_or_exit` (already used everywhere else in this codebase for exactly this purpose) is
what gives "stop on first failure" for free: on `Ok` it returns and the loop continues to the
next name; on `Err` it prints the message (auto-escaped, wrapped in `[red]...[/red]`, exactly
like every other error path in this file) and raises `typer.Exit(code=1)`, ending the command.
No new error-handling pattern is introduced.

Incidental consequence: a few messages that were previously hand-colored *partial*-red (e.g.
`"[red]{target} not found.[/red] Run 'dvt init' first."` — only the first sentence red) now
render as one uniform whole-message-red block via `unwrap_or_exit`, matching how every other
error path in this file already renders. This is a minor style change, not a behavior change —
the plain-text content of every message is unchanged, so no existing test's substring
assertions on `result.output` are affected.

`dvt feature add`'s "auto-sync if the cache is empty" check still happens once, before the
loop begins — not per name. `dvt feature remove` reloads the sidecar fresh inside `_remove_one`
on every call (as it already does today), so `dvt feature remove foo foo` in one invocation
correctly fails on the second `foo` (already removed by the first) rather than treating both as
independent.

### Help text

- `add`: `"Cached feature name(s) to add, applied in order."`
- `remove`: `"Applied feature name(s) to remove, in order."`

## Error handling

Unchanged failure modes, now expressed as `Err(...)` instead of a direct print+exit:
- `add`: `devcontainer.json` missing, not strict JSON, feature already applied, feature not
  cached, merge result schema-invalid, sidecar write failure.
- `remove`: `devcontainer.json` missing, feature not tracked, not strict JSON, recomputed
  result schema-invalid, sidecar write failure.

No partial writes within a single name's add/remove (unchanged — validate before write, exactly
as today). Across multiple names in one invocation: names before the failing one keep their
already-committed writes; the failing name and everything after it are never attempted.

## Testing

- `tests/test_feature_command.py`: every existing single-name test for `add`/`remove` is
  unaffected (a batch of one name is byte-for-byte the same code path).
- New: `dvt feature add` with two valid names applies both, in order, with two success lines
  printed and both present in the sidecar's `applied` list.
- New: `dvt feature add` with a valid name followed by an invalid one (uncached) — the valid
  one is applied (sidecar/devcontainer.json reflect it) and printed as added, the command exits
  1, and the invalid name's failure message appears; a third valid name after the failing one
  (if given) is never attempted (its would-be-success message is absent from output).
- New: same two shapes (`all succeed` / `stops on first failure, earlier successes stick`) for
  `dvt feature remove`, including the `remove foo foo` same-invocation-double-remove case
  (second `foo` fails with the "not tracked" message, since the first removal already dropped
  it from `applied`).
