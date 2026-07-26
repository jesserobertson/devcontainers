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
`podman exec -it` — no port is ever published, and no `sshd` is ever installed
into any image. `dvt up` writes (and `dvt delete` removes) a `Host <name>` block
in `~/.ssh/config` whose `ProxyCommand` runs `dvt ssh --stdio <name>`: a real
`asyncssh`-based SSH server that this process runs against its own stdin/stdout,
bridging the resulting session to `docker`/`podman exec -i` in that container.

`dvt stop <name>` / `dvt delete <name>` find the workspace via its `dvt.workspace`
container label — not a `dvt`-side registry — so they work from any directory.
`delete` leaves the built image cached for a faster `up` next time.

### SSH access: what's verified

`ssh <name>` from a real `ssh` client, against a real running workspace
container, works for both shapes a client can ask for:

- an interactive shell session (`ssh <name>`)
- a single exec command (`ssh <name> "echo hi"`), including its exit code

VS Code's own "Attach to Running Container", part of the Dev Containers
extension, also works — but independently of any of this, since it doesn't go
through SSH at all; it uses the container's labels directly. See
[Concepts](concepts.md)'s compatibility section.

### SSH access: not verified yet

- **VS Code Remote-SSH** — not attempted. It may well work through the
  `ProxyCommand` entry; nobody has checked.
- **JetBrains Gateway** — deliberately deferred, pending manual verification.
  It drives a remote host with a long sequence of exec commands and generally
  wants SFTP, so the missing subsystem below is the likeliest sticking point.

### SSH access: known v1 gaps

- **No PTY is allocated on the container side.** The bridged session runs
  `docker`/`podman exec -i`, not `-it`, so there is no tty to interpret control
  characters: **Ctrl-C does nothing** in an interactive `ssh <name>` session,
  and there's no job control. `dvt ssh <name>` (no `ssh` client involved) uses
  `exec -it` and is unaffected. Full-screen programs may also misbehave for the
  same reason.
- **No SFTP subsystem.** Anything that transfers files over the connection —
  `sftp`, `scp` in its modern SFTP mode, and IDE remote-development backends
  that upload themselves — will not work through the `ProxyCommand` entry.

These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `template`/`project` commands don't.
