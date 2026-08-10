# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 allowance that any release may
include breaking changes while bumping only the minor version — patch releases are fixes
only. Revisit switching breaking changes to a major bump once the project reaches `1.0.0`.

## [Unreleased]

### Added

- `dvt info` — shows the current folder's devcontainer setup (image, applied features) and,
  best-effort, any live workspace tied to it.

### Changed

- `dvt up`/`ssh`/`stop`/`delete`'s `<name>` argument is now optional. When omitted, dvt infers
  it from a workspace already tied to the current folder (via its `devcontainer.local_folder`
  container label) — falling back to the folder's own directory name for `up` if none exists
  yet, or refusing for `ssh`/`stop`/`delete` if none exists.

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
