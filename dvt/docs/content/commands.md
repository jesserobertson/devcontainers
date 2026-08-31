# Command Reference

## Machine-readable output

`init`, `up`, `info`, `stop`, `delete`, `sync`, `feature add`, `feature remove`,
`image set`, and `image unset` all accept `--json`, printing exactly one JSON
object on stdout instead of Rich-formatted text: `{"ok": true,
...command-specific fields}` on success, `{"ok": false, "error": "..."}` on
failure (exit code is unaffected by `--json`). `feature list` and `image list`
keep printing a bare JSON array on success (predates the `{"ok": ...}`
convention) but report failures the same way as every other command. `feature
show` and `image show` always printed their raw JSON pass-through on success
and now accept `--json` too, purely so their *failure* path matches the
shared convention as well. `ssh` has no `--json` mode: without `--stdio` it's
an interactive terminal session, and `--stdio` is a raw SSH byte stream, not
structured output. `run` likewise has no `--json` mode — its stdout is the
exec'd command's own output, passed straight through.

`dvt --describe` prints a JSON manifest of every command (dotted names for `feature`
subcommands, e.g. `"feature add"`) with its description, args (name, kind, type,
required, flags), and — for every `--json`-capable command — its output shape as real
[JSON Schema](https://json-schema.org/) (`output.success` / `output.error`), generated
from the Pydantic models in `devtemplate/cli_output_schemas.py`. Args are
auto-generated from the live Typer/Click definitions and can't drift; the output
schemas are hand-maintained (a command's JSON payload isn't recoverable from Click's
own metadata) but cross-checked against real command output by each command's own
`--json` tests, so drift between the two gets caught in CI. Useful for an agent or
script that wants to discover dvt's callable surface — including validating a
response — without parsing `--help` text or guessing at field names.

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

`--image` accepts either a literal OCI ref or a cached image's name/alias (see `dvt
image list`). If the local image cache is empty, `init` auto-syncs it first —
best-effort: a failed sync (e.g. offline) is silently ignored, `--image` still works
with a literal ref, and `init` has never required network access. Unlike `dvt feature
add`'s auto-sync, a sync failure here is never fatal. The sync is skipped entirely when
`--image` already looks like a literal ref (contains `/` or `:`), so a normal `dvt init`
with the default image never pays for a network round-trip.

## `dvt sync`

Refreshes both the cached feature registry (`templates/` in the configured GitHub
repository — default `jesserobertson/devcontainers`, branch `main`, override with the
`DVT_GITHUB_REPO` / `DVT_GITHUB_BRANCH` environment variables) and the cached image
registry (`images/` in that same repository) from GitHub in one call. Prunes any
previously-synced feature or image that's been removed upstream; never touches a
feature directory or image file you've added by hand. Also clears the local cache of
pulled devcontainer spec Feature artifacts (the OCI ref each template's `features` map
points at, e.g. `.../py-devtools:latest`) — `dvt up` caches those forever once pulled,
so this is the only way to pick up a moved `:latest` upstream without deleting dvt's
data directory by hand.

## `dvt feature`

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

## `dvt image`

### `dvt image list`

Lists cached images with their description and OCI ref. `--json` prints the same data
as a JSON array instead of a table, for scripting.

### `dvt image show <name>`

Prints a cached image's raw metadata (name, description, ref, aliases).

### `dvt image set <name> [--ref <ref>] [--description <text>] [--alias <alias> ...]`

Creates or updates `images/<name>.json` in the current repo checkout — an upsert,
unlike `list`/`show`, which operate on the local XDG cache. `set`/`unset` edit the
source repo directly and must be run from inside a checkout of the devcontainers repo
(refuses, exit 1, if `.git` can't be found in the current directory or any parent). A
brand-new `<name>` requires both `--ref` and `--description`; an existing one only
changes the fields you pass — `--alias`, if passed, replaces the existing alias list
entirely rather than appending to it. `--alias` is repeatable, for every alternate name
the image should also resolve by (see `dvt init --image` below). Doesn't publish to
GitHub or affect the local cache — commit and push (or open a PR) yourself, then `dvt
sync` to pick it up locally like anyone else would.

### `dvt image unset <name>`

Removes `images/<name>.json` from the current repo checkout. Same name resolution and
"local checkout only" caveat as `set`.

## Fuzzy name matching

`dvt feature add`/`remove`/`show` and `dvt image show`/`set`/`unset` all fuzzy-match
the name you pass against the relevant set of known names (the template/image cache for
`add`/`show`, the project's own applied features for `remove`, the repo checkout's
`images/` directory for `set`/`unset`). An exact match is used with no prompt. A
close-but-not-exact match (a typo) prints "No `<label>` named '...'. Did you mean
'...'?" and asks to confirm; `--yes`/`-y` skips the prompt and accepts the closest match
automatically. In non-interactive contexts — `--json`, or anywhere a prompt can't be
answered — a close match is reported as a suggestion inside the error instead of
prompting, so a script never hangs waiting on an unanswerable question. No close match
at all is a plain error listing every known name — except for `dvt image set`, where a
name with no close match is accepted unchanged, since it may be a brand-new image
rather than a typo of an existing one.

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

The workspace name is optional on `up`/`ssh`/`stop`/`delete` (positional `<name>`) and on
`run` (`-n`/`--name`, so the trailing tokens are unambiguously the command) — when omitted,
dvt looks for a workspace already tied to the current folder (via its
`devcontainer.local_folder` container label, not just the folder's own name, so it still
finds a workspace created under a different name). Exactly one match reuses it; for `up`, no
match falls back to the folder's own directory name to create a fresh workspace — unless a
workspace already exists under that name for a *different* folder, in which case `up` refuses
rather than silently resuming someone else's workspace; for `ssh`/`run`/`stop`/`delete`, no
match refuses outright (nothing to act on); more than one match always refuses, listing every
candidate name and asking for an explicit one.

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
bridging the resulting session to `docker`/`podman exec` in that container —
`-it` with a real pty for sessions that requested one, `-i` otherwise.

`dvt run [-n <name>] <command>...` execs a single command in the running container
(`docker`/`podman exec`, `-i` — add `-t`/`--tty` for programs that need a real terminal like
a REPL), streams its stdin/stdout/stderr through, and exits with the command's own status —
no interactive shell. dvt's own options (`-n`/`--name`, `-t`/`--tty`) must come before the
command; everything after is passed through untouched, so `dvt run -n web pytest -q -x` does
what you'd expect. The command runs through the workspace user's login shell
(`"${SHELL:-sh}" -ilc …`, same `$SHELL` fallback as `dvt ssh`) so image shell-startup hooks
fire — this repo's base image gates its per-project `pixi shell-hook` on the shell being
interactive, so a bare non-interactive `sh -c` would miss the project environment entirely.

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
  host-side pseudo-terminal bridged through to `docker`/`podman exec -it`,
  which is what a prompt/banner, Ctrl-C, job control, and correctly-sized
  full-screen programs need — matching `dvt ssh <name>`'s direct (non-SSH)
  path. Exercised end-to-end with a real `asyncssh` client against a real
  spawned pty process: the child sees a genuine tty, bytes round-trip both
  directions, a client-side resize reaches the pty, and the exit code
  propagates. Not yet additionally confirmed with an actual `ssh` binary in
  an interactive terminal against a live container, so the user-visible
  behaviours above (a fish prompt actually appearing, Ctrl-C actually
  interrupting, `vim`/`top` actually redrawing on resize) follow from the
  plumbing being correct rather than from having been watched happen.
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
[Installation](installation.md)); `sync`/`feature`/`image`/`init` commands don't.
