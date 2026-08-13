# Command Reference

## `dvt init <path>`

Scaffolds `<path>/.devcontainer/devcontainer.json` with no features yet: a base image
(`--image`, default `ghcr.io/jesserobertson/base-ubuntu:latest`), `workspaceFolder`/
`workspaceMount`, `remoteUser: dev`, and a `postCreateCommand` that runs `pixi install`
(prefixed with a step that turns on pixi's `detached-environments` config — see
[Concepts](concepts.md)). The scaffolded file's `name` field is set to `<path>`'s own
directory name. Refuses (exit 1, nothing written) if
`<path>/.devcontainer/devcontainer.json` already exists. Also scaffolds a minimal
`pixi.toml` if the target directory doesn't already manage its own dependencies (via
`pixi.toml` or a `pyproject.toml` with a `[tool.pixi]` table) — every feature's
`postCreateCommand` runs `pixi install`, which needs one to install from.

## `dvt feature`

### `dvt feature sync`

Fetches every feature from `templates/` in the configured GitHub repository (default
`jesserobertson/devcontainers`, branch `main` — override with the `DVT_GITHUB_REPO` /
`DVT_GITHUB_BRANCH` environment variables) into the local cache. Prunes any previously-synced
feature that's been removed upstream; never touches a feature directory you've added by
hand.

### `dvt feature list`

Lists cached features with their description and base image. `--json` prints the same
data (plus each feature's OCI Feature ref) as a JSON array instead of a table, for
scripting.

### `dvt feature show <name>`

Prints a cached feature's devcontainer.json overlay.

### `dvt feature add <name>`

Merges a feature's overlay into `./.devcontainer/devcontainer.json` (always cwd-relative).
Auto-syncs first if the local cache is empty. See [Concepts](concepts.md) for the merge
semantics. Refuses to write (file left byte-for-byte unchanged) if:

- `.devcontainer/devcontainer.json` doesn't exist — run `dvt init` first
- it exists but isn't strict JSON (comments/trailing commas aren't supported)
- the feature name isn't cached and syncing doesn't produce it
- the merge result would fail validation against the official devcontainer.json schema

Also records the feature in `.devcontainer/dvt-features.json`, a tracking sidecar that
`dvt feature remove` uses to know what's safe to undo.

### `dvt feature remove <name>`

Un-layers a feature previously added with `dvt feature add`, restoring only the fields
that feature's overlay touched — anything else in the file, including manual edits made
since, is left untouched. Refuses (exit 1, nothing written) if `<name>` isn't tracked in
`.devcontainer/dvt-features.json` (never added via `dvt feature add`, or the file predates
it), or if the recomputed result would fail schema validation.

## `dvt info`

Shows the current folder's devcontainer setup: the project name and base image from
`devcontainer.json`, and its applied features — friendly names from `.devcontainer/dvt-features.json`
if that sidecar exists and has entries, otherwise the raw OCI Feature refs from
`devcontainer.json`'s own `features` map (marked `(untracked)`). Refuses ("run `dvt init`
first") if `.devcontainer/devcontainer.json` doesn't exist. Takes no arguments — always
operates on the current directory, like `dvt feature`.

Then, best-effort: if a container runtime is reachable, reports any live workspace tied to
this folder (via the same `devcontainer.local_folder` label `up`/`ssh`/`stop`/`delete` use to
infer a name below) — its name, running/stopped status, and container name. No runtime
reachable, or none found, is reported plainly rather than as an error; `dvt info` never waits
for a stopped Podman machine to start (unlike `up`/`ssh`/`stop`/`delete`) just to check status.

## Workspace lifecycle

`dvt up <name>` builds an image from cwd's `.devcontainer/devcontainer.json` — pulling
each referenced Feature as a real OCI artifact and baking it into a generated
multi-stage Dockerfile, exactly the way `@devcontainers/cli`/`devpod` themselves
build Features — then runs the container. `<name>` is the tag given to the
resulting workspace, not a path; run `up` from inside the project directory. If a
workspace with that name already exists, `up` starts it (if stopped) or leaves it
running (if already running), unless `devcontainer.json` has changed since that
container was built (compared against the config baked into the container's own
`devcontainer.metadata` label) — in which case `up` refuses and points at
`dvt up --rebuild`, rather than silently reusing a stale image. `up` also refuses if
it can't read what the existing container was actually built from (an unreadable or
missing `devcontainer.metadata` label) — a distinct failure from "config changed",
since dvt can't tell either way and won't guess. A missing or unreadable
`devcontainer.json` *on disk*, on the other hand, does not block resuming — the
drift check is simply skipped and the existing container comes back up as-is.

`--rebuild` forces a from-scratch rebuild regardless of whether anything actually
changed: once the current `devcontainer.json` has been loaded and validated
successfully, it removes the existing container and its cached image tag, then
builds fresh with Docker's build cache and base-image reuse both disabled, so a
moved upstream base image tag is picked up too. A workspace is only ever rebuilt
from its own project's folder — `--rebuild` refuses, leaving the container
untouched, if run from somewhere else. A plain `up` never destroys anything, full
stop; only `--rebuild` does, and only after its own validation has already
succeeded.

`<name>` is optional on `up`/`ssh`/`stop`/`delete` — when omitted, dvt looks for a workspace
already tied to the current folder (via its `devcontainer.local_folder` container label, not
just the folder's own name, so it still finds a workspace created under a different name).
Exactly one match reuses it; for `up`, no match falls back to the folder's own directory name
to create a fresh workspace — unless a workspace already exists under that name for a
*different* folder, in which case `up` refuses rather than silently resuming someone else's
workspace; for `ssh`/`stop`/`delete`, no match refuses outright (nothing to act on); more than
one match always refuses, listing every candidate name and asking for an explicit one.

Feature refs in the `features` map accept either a tag
(`ghcr.io/jesserobertson/devcontainers/cli:latest`) or a digest
(`ghcr.io/jesserobertson/devcontainers/cli@sha256:<digest>`) — both resolve and
pull the same way.

Each Feature's `install.sh` always runs as root, regardless of the base image's
own `USER` — per the devcontainer Features spec, not configurable.

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

- **A single exec command** (`ssh <name> "echo hi"`), including its exit
  code — from a real `ssh` client, against a real running workspace
  container.
- **An interactive shell session** (`ssh <name>`) — bridged and exercised
  end-to-end with a real `asyncssh` client against a real subprocess; not yet
  additionally confirmed with an actual `ssh` binary in an interactive
  terminal against a live container. A bare shell request runs the container
  user's own configured shell (falling back to `sh` if `$SHELL` isn't set)
  rather than a hardcoded `sh`, so an image's shell-startup hooks — e.g. this
  repo's templates activating their project's `pixi` environment via
  `pixi shell-hook` in `.bashrc`/fish's `conf.d` — fire the same way they
  would over a normal interactive login.
- **stdout/stderr stay separate** — the container's stderr arrives on the
  SSH client's own stderr channel, never merged into stdout, so redirecting
  the two separately (`> out.txt 2> err.txt`) works as expected.
- **A real pty for interactive sessions.** A client that requests a pty
  (a plain `ssh <name>`, or `ssh -t <name> <command>`) gets a genuine
  host-side pseudo-terminal bridged through to `docker`/`podman exec -it` -
  a working prompt/banner, Ctrl-C, job control, and correctly-sized
  full-screen programs, matching `dvt ssh <name>`'s direct (non-SSH) path.
  Implemented via the stdlib `pty` module on Linux/macOS and `pywinpty`
  (ConPTY) on Windows. Non-pty exec sessions (`ssh <name> "cmd"`, what VS
  Code Remote-SSH/JetBrains Gateway are expected to rely on) are completely
  unaffected - they keep the separate stdin/stdout/stderr pipes described
  above, since a pty session and a non-pty exec session are structurally
  different: a pty merges stdout+stderr into one stream (standard terminal
  semantics, the same as any real interactive SSH session), which the
  non-pty path deliberately does not do.
- **Multi-byte UTF-8 output survives read-boundary splits** — a run of
  non-ASCII output large enough to land a multi-byte character across two
  underlying reads still decodes correctly on the client side, instead of
  emitting a `�` replacement character at the split.

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

- **No SFTP subsystem.** Anything that transfers files over the connection —
  `sftp`, `scp` in its modern SFTP mode, and IDE remote-development backends
  that upload themselves — will not work through the `ProxyCommand` entry.

These commands require a reachable Docker or Podman engine (see
[Installation](installation.md)); `feature`/`init` commands don't.
