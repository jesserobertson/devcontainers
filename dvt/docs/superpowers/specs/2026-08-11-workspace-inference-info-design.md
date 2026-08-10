# `dvt info` + cwd-based workspace inference

## Problem

`dvt up`/`ssh`/`stop`/`delete` all require an explicit `<name>` argument today, even though
`dvt feature add`/`remove`/`dvt init` already work purely from the current directory with no
argument at all. Running `dvt up` from inside a project means retyping (or copy-pasting) the
project's own name every time. There's also no command that answers "what's the devcontainer
setup for the project I'm standing in, and is a workspace for it running right now" —
`dvt feature list`/`show` describe the registry, not a specific project's live state.

## Goals

- `dvt up`/`ssh`/`stop`/`delete` accept an optional `name`; when omitted, infer it from the
  current directory.
- A new `dvt info` command (no arguments) shows a project's devcontainer config (image, applied
  features) plus, best-effort, the live status of any workspace tied to this folder.
- Inference is based on the `devcontainer.local_folder` container label every workspace `dvt up`
  builds already carries (the same label VS Code's own "Attach to Running Container" uses) —
  not just the directory's own name — so it still finds the right workspace even if it was
  created under a different name than the folder.

## Non-goals

- No change to how workspaces are created/looked-up when an explicit name *is* given — every
  existing invocation (`dvt up my-api`, `dvt ssh my-api`, ...) is unchanged.
- No change to the `dvt.workspace` label or `find_workspace_container`'s exact-name lookup —
  this adds a second, folder-based lookup alongside it, not a replacement.
- No fix for the SSH PTY/prompt gap surfaced during this session's testing (tracked separately,
  as a future brainstorming item — out of scope here).

## Inference mechanism

`container.py` gets a new sibling to `find_workspace_container`:

```python
def find_workspace_containers_by_folder(client: DockerClient, folder: Path) -> list[Container]:
    """Find every container tagged devcontainer.local_folder=folder (resolved, absolute) -
    the same label VS Code's Dev Containers extension uses to recognize a workspace, so a
    dvt-built container is found here even if it was created under a name that doesn't match
    the folder's own."""
```

Filters `containers.list(all=True, filters={"label": f"devcontainer.local_folder={folder.resolve()}"})`
— matching exactly how `compute_labels` writes the label (`str(project_path.resolve())`), so
Windows path casing/separators compare correctly.

A new module, `src/devtemplate/workspace_lookup.py`, turns an optional CLI argument into a
concrete name:

```python
def resolve_for_up(client: DockerClient, name: str | None, cwd: Path) -> Result[str, Exception]:
    """name given -> returned as-is (unchanged behavior). name omitted -> look up by folder:
    one match reuses its name (resuming/rebuilding that workspace, same as passing it
    explicitly); zero matches fall back to cwd's own directory name (to create a fresh
    workspace, matching dvt init's own default-name derivation); multiple matches is an Err
    listing every candidate name, since dvt won't guess which one you meant."""

def resolve_existing(client: DockerClient, name: str | None, cwd: Path) -> Result[str, Exception]:
    """Same shape as resolve_for_up, for ssh/stop/delete - these only ever act on a workspace
    that already exists, so zero matches is also an Err (nothing to act on) rather than a
    directory-name fallback."""
```

Both share a private helper that does the `find_workspace_containers_by_folder` call and
formats the "multiple matches" error message (`"Multiple workspaces match this folder: {names}.
Run 'dvt {command} <name>' with one of these."`, `{command}` supplied by each caller so the
suggested next step names the actual command that was run).

## CLI changes

`cli.py`'s `up`, `ssh`, `stop`, `delete` each change their `name` parameter from
`typer.Argument(..., help=...)` to `typer.Argument(None, help="... (default: inferred from the
current folder)")`, and call the relevant resolver immediately after `get_client(...)` succeeds
— turning `str | None` into a concrete `str` before falling through to each command's existing
(otherwise unchanged) body. `get_client(...)`'s own "no runtime reachable" refusal still runs
first, exactly as today — the resolvers are never reached without a working runtime.

### `dvt info`

New top-level command, `src/devtemplate/commands/info.py`, taking no arguments (matches `dvt
feature`'s existing pattern of always operating on the current directory):

1. Reads `./.devcontainer/devcontainer.json`; refuses ("run `dvt init` first") if it doesn't
   exist — same message shape `feature add`/`remove` already use for a missing target file.
2. Reads `./.devcontainer/dvt-features.json` if present. Prints the project's `name` (from
   devcontainer.json), `image`, and applied features: the sidecar's `applied` list (friendly
   names) if a sidecar exists, otherwise the raw OCI refs from devcontainer.json's own
   `features` map (still meaningful, just not friendly names) if there's no sidecar to name
   them, or nothing if there are no features at all.
3. Best-effort live status: attempts `get_client(...)`; if no runtime is reachable, prints a
   one-line note ("no container runtime reachable — showing local config only") and stops
   there, exit 0 — this is the one command that tolerates a missing runtime, since its local
   half never needed one. If a runtime is reachable, calls
   `find_workspace_containers_by_folder`: zero matches prints "No workspace running for this
   project. Run 'dvt up' to start one." (not an error); one match prints its name, running/
   stopped state, and container name; multiple matches lists all of them (read-only, so no
   need to refuse the way `up`/`ssh`/`stop`/`delete` do).

## Testing

- `tests/test_container.py`: `find_workspace_containers_by_folder` — filters by the resolved
  folder label, returns `[]` when none match, returns all matches when several do (same
  `MagicMock`-based style as the existing `find_workspace_container` tests).
- New `tests/test_workspace_lookup.py`: `resolve_for_up`/`resolve_existing`, each covering
  explicit-name passthrough, one match, zero matches (differing between the two functions),
  and multiple matches (`Err` with every candidate name in the message).
- New `tests/test_info_command.py`: no devcontainer.json (refuses); devcontainer.json with no
  sidecar (shows raw feature refs); with a sidecar (shows friendly names); no runtime reachable
  (local info only, exit 0); runtime reachable with zero/one/multiple matching containers.
- `tests/test_cli.py`: `up`/`ssh`/`stop`/`delete` gain coverage for the omitted-name path —
  one match, zero matches (`up`'s fallback vs. the other three's refusal), and multiple matches
  — on top of their existing explicit-name tests, which are unaffected.
