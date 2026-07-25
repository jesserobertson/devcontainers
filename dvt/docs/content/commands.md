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

## Workspace lifecycle

`dvt up <name>` builds an image from cwd's `.devcontainer/devcontainer.json` — pulling
each referenced Feature as a real OCI artifact and baking it into a generated
multi-stage Dockerfile, exactly the way `@devcontainers/cli`/`devpod` themselves
build Features — then runs the container. `<name>` is the tag given to the
resulting workspace, not a path; run `up` from inside the project directory. If a
workspace with that name already exists, `up` starts it (if stopped) or leaves it
running (if already running) rather than rebuilding — delete and re-`up` to pick up
devcontainer.json changes.

`dvt ssh <name>` execs directly into the running container via `docker exec -it`/
`podman exec -it` — no SSH server is ever installed into any image, no port is
ever published, and no real SSH protocol is involved. No `~/.ssh/config` entry is
written, so plain `ssh <name>`, VS Code Remote-SSH, and JetBrains Gateway are not
supported through `dvt`; `dvt ssh <name>` is the only supported terminal-access
path. (VS Code's own "Attach to Running Container", part of the Dev Containers
extension, still works independently since it doesn't go through SSH at all — it
uses the container's labels directly; see [Concepts](concepts.md)'s compatibility
section.)

`dvt stop <name>` / `dvt delete <name>` find the workspace via its `dvt.workspace`
container label — not a `dvt`-side registry — so they work from any directory.
`delete` leaves the built image cached for a faster `up` next time.

These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `template`/`project` commands don't.
