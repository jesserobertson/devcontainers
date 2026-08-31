# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 allowance that any release may
include breaking changes while bumping only the minor version — patch releases are fixes
only. Revisit switching breaking changes to a major bump once the project reaches `1.0.0`.

## [Unreleased]

### Changed

- `--describe` now works on every command and command group, not just the top level:
  `dvt up --describe` emits the manifest for just `up`, `dvt feature --describe` just the
  `feature` subtree, and `dvt --describe` the whole tree as before. The flag rides the
  subcommand the same way `--help` does, so an agent discovering one command no longer has
  to parse (or pay the tokens for) the entire CLI surface. Output shape is unchanged —
  `{"dvt_version": ..., "commands": {...}}` — just scoped. The mechanism lives in a new
  self-contained `devtemplate.describe` module (`describe.Typer`) so it can be lifted into
  its own package later.

## [0.4.0] - 2026-08-31

### Added

- `dvt run [-n <name>] <command>...` — run a single command inside a running workspace and
  exit with its status, without opening an interactive shell. Resolves the workspace the
  same way `dvt ssh`/`stop`/`delete` do (folder-inferred when `-n` is omitted). The command
  runs through the workspace user's login shell so image shell-startup hooks (e.g. a
  project's pixi environment) apply; pass `-t`/`--tty` for programs that need a real
  terminal.
- `dvt image` — a new command group (`list`, `show`, `set`, `unset`) for managing the
  base images dvt knows about, mirroring `dvt feature`'s list/show shape. `set`
  (create-or-update) and `unset` edit `images/*.json` directly in a checkout of the
  devcontainers repo (must be run from inside one); `list`/`show` work from the local
  synced cache.
- `dvt sync` — replaces the old `dvt feature sync`/`dvt image sync`, refreshing both the
  cached feature and image registries from GitHub in one call.
- `dvt init --image` now accepts a cached image's name or alias, not just a literal OCI
  ref, and auto-syncs the image cache first if it's empty (best-effort — a failed sync
  never blocks `init`, which has never required network access).
- Fuzzy name matching on `dvt feature add`/`remove`/`show` and `dvt image
  show`/`set`/`unset`: a mistyped name gets a "Did you mean X?" prompt (`--yes`/`-y` to
  auto-accept), and fails with a suggestion instead of hanging in non-interactive/
  `--json` contexts. `dvt image set` on a name with no close match is accepted
  unchanged instead, since it may be a brand-new image rather than a typo.

## [0.3.1] - 2026-08-16

### Fixed

- PyPI distribution renamed from `devtemplate` to `dvt-cli` — PyPI rejected `devtemplate` as too
  similar to an existing project name. Only the PyPI package name changed; the `dvt` command and
  the `devtemplate` Python module are unaffected.

## [0.3.0] - 2026-08-16

### Added

- `dvt feature add`/`remove` now accept one or more names, applied/removed in order, stopping at
  the first failure — everything before it stays applied. A single name still behaves
  byte-for-byte identically to before.
- `dvt info` — shows the current folder's devcontainer setup (image, applied features and their
  descriptions) and, best-effort, any live workspace tied to it.
- `dvt up --rebuild` — tears down and rebuilds a workspace from scratch. `dvt up` also now
  detects when an existing workspace's container was built from a different `devcontainer.json`
  than what's currently on disk and refuses to resume it rather than silently resuming stale
  config, pointing at `--rebuild`.
- `dvt ssh` sessions that request a pty (interactive `ssh <name>`, or `ssh -t <name> <cmd>`) now
  get a real host-side pseudo-terminal bridged through to the container, fixing a
  missing-prompt/no-job-control gap. Non-pty exec sessions (`ssh <name> "cmd"`) are unaffected.
- `--json` output across commands, plus a `--describe` machine-readable tool manifest.
- `--verbose`/`-v` and `--debug` global flags — surface logerr's Result-error logging (and any
  other loguru output) on stderr at INFO or DEBUG level respectively; `--debug` wins if both are
  given. Neither is set by default, so `dvt` stays silent as before.
- `dvt up` now shows a spinner with a live stage label (pulling Features, building the image,
  starting the container, ...) instead of sitting silently while Docker/network calls run.
- `dvt stop`/`delete`/`feature sync`/`feature add`'s auto-sync now also show a spinner while they
  run, matching `up` - previously only `up` did.
- GHCR Feature fetches (`feature sync`/`add`'s underlying pulls) and the post-machine-start podman
  ping now retry past a transient failure instead of failing the whole command outright.

### Changed

- `dvt up`/`ssh`/`stop`/`delete`'s `<name>` argument is now optional. When omitted, dvt infers
  it from a workspace already tied to the current folder (via its `devcontainer.local_folder`
  container label) — falling back to the folder's own directory name for `up` if none exists
  yet (refusing instead if that name is already taken by a *different* folder's workspace), or
  refusing for `ssh`/`stop`/`delete` if none exists.

## [0.2.0] - 2026-08-10

### Changed

- Flattened the CLI: `dvt project init`/`dvt project add-feature` and
  `dvt template {sync,list,show}` are replaced by `dvt init` and
  `dvt feature {list,show,sync,add,remove}`. No aliases kept — this is a breaking change.
- `dvt init` no longer scaffolds from a template; it writes a minimal boilerplate
  `devcontainer.json` (default base image `ghcr.io/jesserobertson/base-ubuntu:latest`,
  overridable with `--image`) with no features. Use `dvt feature add` to layer features on
  afterward.

### Added

- `dvt feature remove` — un-layers a previously added feature, restoring only the fields it
  touched (tracked via a new `.devcontainer/dvt-features.json` sidecar), leaving any other
  hand-edited fields untouched.
- `dvt feature list --json` — machine-readable feature registry listing; each entry includes
  a human-readable `description` (new field, backfilled onto every
  `templates/<name>/devcontainer.json`).
- `dvt --version`.

## [0.1.0] - 2026-07-23

Initial release: `dvt up`/`ssh`/`stop`/`delete` workspace lifecycle, `dvt project init`/
`add-feature`, `dvt template sync`/`list`/`show`.
