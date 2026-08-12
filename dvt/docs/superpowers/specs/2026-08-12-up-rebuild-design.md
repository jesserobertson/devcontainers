# `dvt up --rebuild`: detect config drift, rebuild on demand

## Problem

`up_workspace` (`src/devtemplate/workspace.py`) only builds+runs from scratch when no container
with the target's `dvt.workspace` label exists yet. If one already exists, `dvt up` either starts
it (stopped) or leaves it running (already running) — it never rebuilds, even if
`.devcontainer/devcontainer.json` changed since the image was built. This is documented as a v1
limitation ("delete and re-`up` to pick up changes"), but it's easy to hit silently: `dvt feature
add`/`remove` make editing `devcontainer.json` a normal, frequent operation, and neither command
warns that a running/existing workspace won't reflect the change. Concretely reported: adding
`py-devtools` via `dvt feature add`, running `dvt up` again, `ruff` not being available in the
container — the stale image was reused with no indication anything was wrong.

Separately, `dvt delete` only removes the container, deliberately leaving the built image cached
for a faster next `up`. That's fine when nothing changed, but there's currently no `dvt` command
that forces a genuinely fresh build (new upstream base image, changed Dockerfile-equivalent
inputs) short of removing the cached image by hand with a raw `docker`/`podman` command.

## Goals

- `dvt up` detects when an existing workspace's container was built from a different
  `devcontainer.json` than what's on disk right now, and refuses to silently resume it.
- A new `dvt up --rebuild` flag rebuilds a workspace from scratch: removes the existing
  container, drops the cached image tag, and forces a cache-free, freshly-pulled image build.
- `--rebuild` also serves as the general "force fresh" escape hatch (e.g. a moved upstream base
  image tag) — it isn't gated on drift being detected; it always means "throw away and rebuild."

## Non-goals

- No change to `dvt feature add`/`remove` — they keep editing `devcontainer.json` only, with no
  awareness of any live workspace. The next `dvt up` is where drift becomes visible; a user just
  finds out one command later than they might otherwise, which is an acceptable tradeoff for
  keeping the two concerns (editing the file vs. reconciling a running container with it) in one
  place.
- No new state file, hash cache, or sidecar field for tracking "what a container was built from."
  The `devcontainer.metadata` container label `compute_labels` already writes covers it.
- No fix for the SSH PTY/prompt gap (tracked separately as its own future brainstorm).
- No partial/incremental rebuild (e.g. only re-running lifecycle commands). `--rebuild` always
  means a full teardown and fresh `build_image`/`run_container` sequence.

## Drift detection mechanism

`container.py`'s `compute_labels` already base64-encodes the full resolved `devcontainer.json`
into a `devcontainer.metadata` label on every container it builds. That's reused as the sole
source of truth for "what was this container built from" — no separate hashing/state-file
scheme.

The encode step is factored out into a shared helper:

```python
def _encode_metadata(config: dict[str, Any]) -> str:
    """base64(json.dumps(config)) - the exact devcontainer.metadata label value."""
```

`compute_labels` calls it internally (behavior unchanged). Two new functions sit alongside it:

```python
@wrap_result
def read_stored_config(container: Container) -> dict[str, Any]:
    """Decode a container's devcontainer.metadata label back into a dict. Errs if the
    label is missing or not valid base64/JSON - defensive against a foreign or
    corrupted container, since every container dvt itself builds always carries it."""

def config_has_drifted(container: Container, config: dict[str, Any]) -> bool:
    """True if container's stored config differs from config (current on-disk
    devcontainer.json, already parsed). Dict equality, not string equality - key
    order is not meaningful. A read_stored_config failure counts as drifted: better
    to ask for --rebuild than silently resume a container we can't verify."""
```

Comparison is dict equality (`stored != config`), not a raw string/label comparison, since JSON
key order can legitimately differ between the config a container was originally built from and a
re-parsed read of the current file (e.g. `merge_layer`'s output ordering) without the config
having meaningfully changed.

## `up_workspace` changes

`up_workspace` gains a `rebuild: bool = False` parameter. The existing-container branch changes
from unconditionally calling `_resume_existing` to:

1. Load and validate the current on-disk config the same way the fresh-build path already does
   (`_load_config` + `refuse_unsupported`) — needed here now too, to have something to compare
   against.
2. If `rebuild` is `False`:
   - `config_has_drifted(existing, config)` is `False` → today's behavior, unchanged:
     `_resume_existing` (start if stopped, refresh SSH config, return).
   - `True` → `Err(ValueError(...))` naming the top-level keys that differ (symmetric difference
     of keys whose values differ between stored and current config) and instructing
     `dvt up --rebuild`. `existing` is never touched.
3. If `rebuild` is `True` (regardless of drift): tear down `existing` (see below), then fall
   through into the same fresh-build sequence already used when no container exists at all.

If `rebuild` is `True` and no existing container was found in the first place, teardown is
simply skipped — `--rebuild` on a first `up` is just an ordinary fresh build.

### Teardown (rebuild only)

1. `existing.remove(force=True)` — same call `dvt delete` already makes, wrapped so a
   docker/podman `APIError` becomes an `Err` rather than propagating raw.
2. Drop the cached image tag: `handle.client.images.remove(f"dvt/{name}:latest", force=True)`,
   wrapped. **Non-fatal on failure** — print a dim warning and continue. This step is for
   `docker images` hygiene; the upcoming build overwrites the tag regardless of whether the old
   one was explicitly removed first.
3. `build_image` gains `nocache: bool = False` and `pull: bool = False` parameters, passed
   through to `client.images.build(..., nocache=nocache, pull=pull)`. `up_workspace` sets both
   `True` only on the rebuild path. This — not the image-tag removal — is what actually forces
   freshness: Docker's build cache is keyed by instruction content, not by output tag, so
   deleting `dvt/{name}:latest` alone would not stop the next build from reusing cached layers or
   an already-local base image.

No explicit SSH config cleanup is needed before teardown: `_refresh_ssh_config` already runs at
the end of the fresh-build path, and `write_ssh_config_entry` already upserts by name (the same
call `_resume_existing` uses).

## CLI changes

`cli.py`'s `up` command gains:

```python
rebuild: bool = typer.Option(False, "--rebuild", help="Force a fresh rebuild, discarding the existing container and cached image.")
```

threaded straight through as `up_workspace(handle, settings, resolved_name, Path.cwd(), rebuild=rebuild)`.

**Refusal message** (drift detected, no `--rebuild`):

```
Workspace 'fastapi' already exists but its devcontainer.json has changed since it was
built (features, postCreateCommand). Run 'dvt up --rebuild' to rebuild it, or revert
devcontainer.json and run 'dvt up' again. To use the existing container without going
through 'up' at all, run 'dvt ssh fastapi'.
```

If the drift check itself failed (unreadable label), the message says so instead of listing
keys: `"...but dvt couldn't verify its config (<reason>). Run 'dvt up --rebuild' to rebuild it."`

**Success message**: unchanged — the existing `dvt up` success line applies equally whether the
container was resumed, freshly built, or rebuilt, since by the time it prints the workspace is
just "up."

## Error handling

- **Drift check fails** (missing/malformed label): treated as drifted, per `config_has_drifted`
  above — refuses rather than crashing or silently resuming.
- **`--rebuild`, no existing container**: teardown skipped, ordinary fresh build.
- **Container removed, image removal fails**: non-fatal, warns and continues to build.
- **Build/run fails after teardown**: surfaces as `Err` exactly like today's fresh-build failure
  path. Worth stating plainly: unlike today's resume path (which never destroys anything), a
  failed `--rebuild` can leave the user with no workspace at all — the old container is already
  gone before the new build is attempted. This is inherent to "rebuild means tear down first,"
  not a bug, but should be documented in `commands.md` so it isn't a surprise.

## Testing

Following `tests/test_workspace.py`'s existing per-function-monkeypatch style:

- `tests/test_container.py`: `read_stored_config` (round-trips a real `compute_labels` output;
  errs on a missing/garbage label) and `config_has_drifted` (identical dict → `False`; a changed
  key → `True`; unreadable label → `True`).
- `tests/test_workspace.py`:
  - Existing tests that construct `existing = MagicMock()` without a `.labels` need updating to
    set `existing.labels` to a real `compute_labels(...)` result, since the drift check now runs
    on the resume path too.
  - Unchanged config, no `--rebuild` → resumes as today.
  - Changed config, no `--rebuild` → `Err` naming the changed key(s); `existing.remove` never
    called.
  - Changed config, `--rebuild` → `existing.remove(force=True)` called, image removal attempted,
    then the fresh-build sequence runs (reusing the `build_image`/`run_container`/etc.
    monkeypatches from `test_up_workspace_full_build_and_run_sequence`), with `nocache=True,
    pull=True` asserted in the `build_image` call.
  - `--rebuild`, no existing container → teardown skipped (`existing.remove` not referenced),
    fresh build still happens.
  - Image-removal failure during `--rebuild` → build still proceeds.
- `tests/test_cli.py`: `--rebuild` on `up` threads through to `up_workspace(..., rebuild=True)`,
  matching the existing pattern of asserting call args against a mocked `up_workspace`.

## Docs

`docs/content/commands.md`'s `up` section and "SSH access: known v1 gaps"-style notes get updated
to describe `--rebuild` and drop the now-outdated "delete and re-`up` to pick up changes"
guidance (replaced with "or `dvt up --rebuild`").
