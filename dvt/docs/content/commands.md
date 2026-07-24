# Command Reference

## `dvt template`

### `dvt template sync`

Fetches every template from `templates/` in the configured GitHub repository (default
`jesserobertson/devcontainers`, branch `main` — override with the `DVT_GITHUB_REPO` /
`DVT_GITHUB_BRANCH` environment variables) into the local cache. Prunes any previously-synced
template that's been removed upstream; never touches a template directory you've added by
hand.

### `dvt template list`

Lists cached templates with their base image and declared feature.

### `dvt template show <name>`

Prints a cached template's `devcontainer.json`.

## `dvt project`

### `dvt project init <path> --template <name>`

Scaffolds `<path>/.devcontainer/devcontainer.json` from a cached template. Auto-syncs first
if the cache is empty, or always if `--refresh` is passed. The scaffolded file's `name` field
is set to `<path>`'s own directory name, not the template's. Refuses (exit 1, nothing
written) if `<path>/.devcontainer/devcontainer.json` already exists, or if the template
itself doesn't pass schema validation.

### `dvt project add-feature <name>`

Merges another feature's template into `./.devcontainer/devcontainer.json` (always
cwd-relative). See [Concepts](concepts.md) for the merge semantics. Refuses to write (file
left byte-for-byte unchanged) if:

- `.devcontainer/devcontainer.json` doesn't exist
- it exists but isn't strict JSON (comments/trailing commas aren't supported)
- the feature name isn't cached (run `dvt template sync` first)
- the merge result would fail validation against the official devcontainer.json schema

## Lifecycle passthroughs

`dvt up`, `dvt ssh`, `dvt stop`, `dvt delete` all forward directly to the equivalent `devpod`
command, passing through any extra arguments and the real exit code unmodified — `dvt ssh
my-project -- pytest` returns `pytest`'s actual exit code, not something `dvt` interprets.
These require `devpod` on `PATH` and a working container runtime; if `devpod` can't be found,
`dvt` reports a clean error rather than a raw traceback (this failure mode is NOT retried —
unlike `template sync`'s GitHub calls, a devpod exit code is meaningful output to forward,
not a transient error).

Any extra arguments that look like flags (start with `-`) need a `--` separator before them,
so Typer forwards them instead of trying to parse them as `dvt`'s own options — e.g.
`dvt up my-project -- --id my-project --ide none`, not `dvt up my-project --id ...`.

```bash
dvt up <path-or-workspace-name> [-- extra devpod args]
dvt ssh <workspace-name> [-- command]
dvt stop <workspace-name>
dvt delete <workspace-name>
```
