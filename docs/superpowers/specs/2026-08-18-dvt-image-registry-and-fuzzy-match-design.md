# dvt: image registry + fuzzy name matching

**Date:** 2026-08-18
**Status:** Approved

## Overview

Two related additions to `dvt`:

1. A `dvt image` subcommand that gives the two base images this repo builds
   (`base-ubuntu`, `base-cuda`) the same curated-registry treatment `dvt feature`
   already gives templates — synced from GitHub for reading, edited as local files for
   writing.
2. A reusable fuzzy-name-matching capability, so mistyping a feature or image name
   (`dvt feature add fastpi`) gets a `Did you mean 'fastapi'? [y/n]` prompt instead of a
   flat lookup failure, wired identically across every command that takes a "pick one of
   these known names" argument.

Neither is a natural extension of a single existing flow (there's no "image metadata"
concept anywhere today — images are literal OCI-ref strings hardcoded in each template's
`devcontainer.json`), which is why this gets a spec rather than an in-chat bounded design.

## Fuzzy matching capability

`devtemplate/fuzzy.py`, two pieces:

**`resolve_or_confirm(query, candidates, *, console, label, assume_yes=False, interactive=True) -> Result[str, Exception]`**
The primitive. Exact match against `candidates` returns immediately with no prompt.
Otherwise `difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)` (stdlib, no new
dependency):

- No match at all → `Err` listing the known names for `label` (e.g. "feature").
- A match, `assume_yes=True` → `Ok(match)`, no prompt.
- A match, `interactive=False` (non-interactive: `--json` or not a TTY) → `Err` naming the
  suggested match in the message, so scripts fail loudly instead of hanging on a prompt
  that will never be answered.
- A match, interactive → `typer.confirm("Did you mean '<match>'?")`; yes → `Ok(match)`,
  no → `Err("no <label> named '<query>'")`.

**`fuzzy_argument(param, *, candidates_fn, label, console)`**
A decorator for the common case: a typer command's `param` (a `str` or `list[str]`
argument/option) should be resolved against a known name list before the command body
runs. It:

- Injects a standardized `--yes`/`-y` option into the wrapped function's signature (via
  `inspect.signature(...).replace(...)`, the same `__signature__`-override trick Click/
  Typer decorators commonly use — transparent to Typer's own signature-based CLI
  generation).
- At call time, loads `Settings` and calls `candidates_fn(settings)` to get the current
  name list, resolves `param`'s value(s) through `resolve_or_confirm`, and substitutes
  the resolved value(s) back into `kwargs` before calling the wrapped function.
- `candidates_fn` is typically an existing lookup passed directly with no wrapper, e.g.
  `candidates_fn=list_cached_templates`.

Applied to `feature add`/`remove`/`show` (`candidates_fn=list_cached_templates`) and
`image show`/`update`/`delete` (`candidates_fn=list_cached_images`) — every case where
the argument *is* the answer once resolved.

`init.py --image` does not use the decorator: resolving an image argument means mapping
name-*or*-alias-*or*-literal-ref → ref, not name → itself, so `images.py` calls
`resolve_or_confirm` directly inside its own `resolve_image_ref()` (see below) rather than
forcing that extra mapping through a decorator built for the flat case.

## Image registry

### Repo side

New `images/` directory (sibling to `templates/`, `features/`), one JSON file per image:

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

Scoped to just these two curated images (not arbitrary third-party images) — matches
what this repo actually builds today, extensible later by adding another file.

### `dvt` side — read path (sync from GitHub)

Mirrors `store.py`/`github.py`'s existing template-sync mechanism exactly:

- `github.py` gains `list_image_names` (GitHub Contents API on `images/`, filtered to
  `.json` files) and `fetch_image_metadata` (raw-content fetch of `images/<name>.json`),
  same retry/error handling as `list_template_names`/`fetch_template`.
- New `devtemplate/images.py` (mirrors `store.py`): `sync_images`, `list_cached_images`,
  `load_cached_image`, with their own manifest file (`image_manifest.json` in the XDG
  data dir, alongside the existing `manifest.json` for templates) so pruning stays
  independent of the template sync. Caches to a new `Settings.images_dir` property
  (`data_dir / "images"`, alongside `templates_dir`/`features_dir`).
- `resolve_image_ref(query, cached_images, *, console, assume_yes, interactive) ->
  Result[str, Exception]` in `images.py`: exact match against any cached image's `ref` →
  passthrough (this is today's behavior, unchanged, since `init.py`'s `DEFAULT_IMAGE` is
  already a full ref); exact match against `name` or an `alias` → resolve to that image's
  `ref`, no prompt (unambiguous); otherwise fuzzy-match the query against the combined
  name+alias list via `resolve_or_confirm` and map a resolved name back to its `ref`. If
  the local image cache is empty (never synced) or nothing matches at all, the query is
  returned as-is — this feature only ever helps, it never blocks a literal ref that used
  to work.

### `dvt` side — write path (local file CRUD, no GitHub write access)

`dvt image create <name> --ref --description [--alias ...]` / `update <name> [...]` /
`delete <name>` operate on a git working tree, not the GitHub API:

- Find the repo root by walking up from `cwd` looking for a `.git` directory (new small
  helper, no existing equivalent in `dvt` today since nothing else needs repo-root
  discovery).
- `create` writes `<repo_root>/images/<name>.json`; errors if it already exists (points
  at `update` instead). `update` edits the fields given (leaves others untouched).
  `delete` removes the file.
- Every write command prints a reminder that this only changed the local working tree —
  `git add`/`commit`/`push` (or open a PR) is a manual step, exactly like publishing a new
  feature or template today. No GitHub token, no API write calls, no new security
  surface in `dvt` itself.
- These three are maintainer-facing (there are two curated images; end users installing
  `dvt` via `pipx` won't run them without a checkout of this repo), unlike `sync`/`list`/
  `show`, which work for anyone regardless of whether they have a local checkout.

### Command surface

| Command | Behavior |
|---|---|
| `dvt image sync` | Refresh the local image cache from GitHub (mirrors `feature sync`) |
| `dvt image list [--json]` | Table of cached images: name, description, ref |
| `dvt image show <query> [--yes]` | Fuzzy-resolve `<query>` against cached names, print the image's raw JSON metadata |
| `dvt image create <name> --ref --description [--alias ...]` | Write `images/<name>.json` in the current repo checkout |
| `dvt image update <name> [--ref] [--description] [--alias ...]` | Edit fields on the existing repo-local file |
| `dvt image delete <name> [--yes]` | Remove the repo-local file |

`create`/`update`/`delete` bypass the `fuzzy_argument` decorator on their own `<name>`
argument for `create` (a new name is being coined, no candidates to resolve against) but
use it for `update`/`delete` (they must reference an existing cached name).

`cli.py`: `app.add_typer(image_app, name="image")`.

`init.py`: the `--image` option's value now goes through `resolve_image_ref` (using the
locally cached image registry) before being written into the scaffolded
`devcontainer.json`; gains a `--yes`/`-y` flag (independent of `fuzzy_argument`, since
this is the alias/ref-aware path, not the decorator) to auto-accept a fuzzy match without
prompting.

## Implementation style

`match`/`case` wherever branching naturally maps to it, consistent with this codebase's
existing use of structural pattern matching on `logerr`'s `Ok`/`Err` (e.g.
`commands/feature.py`'s `list_features`). Concretely: `resolve_or_confirm`'s branch on
`difflib.get_close_matches`'s result (`[]` vs `[match]`) and any `Ok(...)`/`Err(...)`
consumption in the new `image` commands use `match`/`case` rather than `if`/`elif`
chains, matching how `feature.py` already consumes `load_cached_template`'s `Result`.

## Error handling

Every new failure path returns through the existing `Result`/`unwrap_or_exit` convention
already used throughout `dvt` — no new error-handling style introduced. Non-interactive
mode (`--json`, or `sys.stdin`/`sys.stdout` not a TTY) never calls `typer.confirm`; it
always resolves to either an unambiguous exact/alias match or an `Err` carrying the
suggested correction in its message, so CI/scripted use never hangs on an unanswerable
prompt.

## Testing

Mirrors this repo's existing per-module test file convention
(`test_store.py`/`test_github.py`/`test_feature_command.py`):

- `test_fuzzy.py` (new): `resolve_or_confirm`'s exact/fuzzy/no-match/non-interactive/
  assume-yes branches; `fuzzy_argument` applied to a throwaway typer command in the test
  file itself, exercised via Typer's `CliRunner`.
- `test_images.py` (new, mirrors `test_store.py`): sync/list/load/manifest-prune
  behavior, plus `resolve_image_ref`'s ref-exact / name-exact / alias-exact / fuzzy /
  empty-cache branches.
- `test_image_command.py` (new, mirrors `test_feature_command.py`): CLI-level tests for
  `sync`/`list`/`show`/`create`/`update`/`delete`, including the repo-root-discovery
  helper (temp dir with a `.git` marker).
- `test_github.py`: extended with `list_image_names`/`fetch_image_metadata` cases, same
  `httpx` mock-transport pattern as the existing template fetch tests — no real network
  calls.
- `test_feature_command.py`: extended for the `fuzzy_argument`-wrapped `add`/`remove`/
  `show` — mistyped name prompts and resolves; `--yes` skips the prompt; non-interactive
  mode fails with the suggestion in the message instead of hanging.
- `test_init.py` (extended, or new if it doesn't exist yet): `--image` resolution against
  a populated image cache — bare name, alias, close-typo, literal ref (unchanged
  passthrough), empty cache (unchanged passthrough).

## Out of scope for v1

- Third-party/arbitrary image registration — scoped to the two images this repo actually
  builds (see Repo side above); revisit if there's ever a reason for users to register
  their own images through this same mechanism.
- `dvt image create/update/delete` writing directly to GitHub via API/token — local-file
  CRUD plus a manual `git push`/PR only, matching how features and templates are already
  published today.
- Fuzzy matching on anything other than "pick one of these known names" arguments (e.g.
  no fuzzy matching on workspace names for `up`/`ssh`/`stop`/`delete` — those come from
  live container state, not a curated registry, and a wrong guess there is a much higher-
  stakes mistake than picking the wrong feature/image name).
