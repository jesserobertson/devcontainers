# Concepts

## Two things called "feature"

`dvt` and the devcontainer spec both use the word "feature," for two different things, and a
single `dvt feature add <name>` often touches both at once:

- **A dvt feature** is one of the curated overlays under this repo's `templates/` directory
  (`fastapi`, `agent`, `pytorch`, ...), fetched by `dvt feature sync` and applied with `dvt
  feature add <name>`. It's a devcontainer.json *fragment* — image, mounts,
  `postCreateCommand`, and so on — merged into your project's `devcontainer.json` at add-time,
  per the [merge algorithm](#the-merge-algorithm) below. Nothing is downloaded or built when
  you run `add`; it's a JSON edit.
- **A devcontainer spec Feature** (capital F, per the [spec](https://containers.dev/implementors/features/))
  is an OCI artifact — an `install.sh` plus metadata — referenced by ref in devcontainer.json's
  own `features` map, and installed into the image by `dvt up` (or `@devcontainers/cli`, or any
  other spec-compliant tool) when the container is built.

A dvt feature template usually references a matching spec Feature. `templates/fastapi/devcontainer.json`
sets `postCreateCommand`, mounts a pixi cache volume, *and* adds
`"ghcr.io/jesserobertson/devcontainers/fastapi:latest"` to `features` — so `dvt feature add
fastapi` edits your `devcontainer.json` in one step (JSON merge, instant, no network beyond the
sync), while the later `dvt up` is what actually pulls that OCI ref and bakes it into the image.

The distinction matters for *when* things happen: `init`/`feature add`/`feature remove` only
ever touch `devcontainer.json` and its tracking sidecar (see below) — no Docker/Podman is
involved, and they work with no container runtime installed at all. `up` is the only command
that talks to a runtime, and it's also the only one that resolves spec Features.

## The merge algorithm

`dvt feature add` layers a new feature's template onto an existing project's
`devcontainer.json` using a field-typed merge (ported from
[`dev`](https://github.com/squirrelsoft-dev/dev)'s Rust implementation), not a generic deep
merge:

| Field(s) | Rule |
|---|---|
| `name`, `image`, `remoteUser`, `waitFor`, `shutdownAction` | scalar — the new feature's value overrides |
| `features` | union by key — new feature's entry wins on collision |
| `mounts`, `forwardPorts` | concatenate, deduplicated |
| `runArgs` | concatenate **without** dedup — repeated flags (e.g. multiple `--env-file`) are legitimate |
| `remoteEnv`, `containerEnv` | map merge — new feature's keys win on collision |
| `postCreateCommand`, `postStartCommand`, `postAttachCommand`, `onCreateCommand`, `updateContentCommand`, `initializeCommand` | union only if both sides use the named-command-object form; otherwise the new feature's value replaces outright |
| anything else | new feature's value wins |

Fields the project already has that the new feature's template doesn't mention are left
exactly as they are.

## Why `name`/`workspaceFolder`/`workspaceMount`/`description` are stripped first

Before merging, `add` removes `name`, `workspaceFolder`, `workspaceMount`, and `description`
from the incoming feature template. Every template in this repo sets its own `name` to its
own feature name — `templates/agent/devcontainer.json` literally has `"name": "agent"`.
Applying the merge rule above unfiltered would silently rename your project to whatever
feature you just added. `workspaceFolder`/`workspaceMount` are identical across every
template anyway, so stripping them costs nothing. `description` is feature-registry
metadata used by `dvt feature list`/`show` — it isn't part of the devcontainer.json spec at
all, and the schema is closed to unknown top-level keys, so leaving it in would fail
validation outright.

## Schema validation on write

Before writing, `dvt feature add` and `dvt feature remove` validate the result against a
vendored copy of the official [devcontainer.json base
schema](https://github.com/devcontainers/spec/blob/main/schemas/devContainer.base.schema.json).
If validation fails, nothing is written — the target file is left exactly as it was. This
matters because `DVT_GITHUB_REPO` is user-overridable: a malicious or just-broken fork's
templates get caught before they ever touch your project. `dvt init` performs no schema
validation of its own — it writes fixed, known-valid boilerplate (see [Command
Reference](commands.md)), so there's nothing to validate.

## The `.devcontainer/dvt-features.json` tracking sidecar

`dvt feature add` and `dvt feature remove` maintain a small sidecar file,
`.devcontainer/dvt-features.json`, alongside `devcontainer.json`. It tracks two things:

- `applied` — the ordered list of features currently layered on, each paired with the exact
  overlay (the feature's template, minus the identity/metadata fields above) that was merged
  in when it was added.
- `init` — a snapshot of `devcontainer.json`'s contents from immediately before the current
  run of applied features began (re-captured whenever `applied` is empty — so also after
  removing every feature back down to none), whether that's `dvt init`'s original
  boilerplate, a hand-written file, or either of those plus hand-edits made before that
  `dvt feature add`.

`dvt feature remove <name>` uses this to replay a scoped per-field merge across `init` and
every *other* still-applied feature's overlay, restricted to just the fields `<name>`'s own
overlay touched, then writes only those fields back to `devcontainer.json` — everything else
in the file is left completely alone.

The precise guarantee this gives you: a field the removed feature's overlay never touched is
always left alone, including any hand-edit you made to it, no matter when you made it. A
field the overlay *did* touch is restored to whatever `init` plus the remaining features'
overlays say it should be — so a hand-edit to a field a *later*-added feature also touches,
made *between* two separate `dvt feature add` calls, is not preserved if that later feature
is removed. Only edits made before the *first* `add` (captured in `init`), or edits to
fields no tracked feature ever touches, are guaranteed to survive a `remove`.

### When `dvt feature remove` refuses because nothing is tracked

`remove <name>` only works for features `dvt feature add` itself applied — it refuses if
`.devcontainer/dvt-features.json` doesn't exist yet, or exists but doesn't list `<name>`
(e.g. it was merged in by hand, or the sidecar was deleted or never committed). dvt doesn't
guess at what an untracked feature's overlay might have been, since re-deriving it from the
feature's *current* cached definition could silently differ from whatever was actually
merged in originally. In that situation you have two options: edit `devcontainer.json` by
hand to remove the feature's fields yourself, or rebuild tracking from a clean slate — back
up `devcontainer.json`, delete it, run `dvt init`, then `dvt feature add <name>` for each
feature you want (this restarts tracking correctly, but any manual customization on the old
file won't carry over automatically).

## Compatibility with other devcontainer tooling

Containers `dvt up` runs carry the same labels other devcontainer-aware tooling
looks for — `devcontainer.metadata` (base64-encoded JSON of the merged config),
`devcontainer.local_folder`, and `devcontainer.config_file` — plus `dvt.workspace`,
the label `ssh`/`stop`/`delete` filter on. This means VS Code's own "Attach to
Running Container" command (part of the Dev Containers extension, no `devpod`
needed) recognizes and can introspect a workspace `dvt` built, and the images `dvt`
builds are normal, standalone images usable by anything with a Docker or Podman
client — not `dvt`-specific artifacts.

This is compatibility, not full spec parity. `dvt` does not implement:

- **docker-compose devcontainers** (`dockerComposeFile`) — image-only devcontainer.json
- **Feature dependency ordering** (`installsAfter`/`dependsOn`) — one Feature per
  devcontainer.json is assumed
- **`build.dockerfile`-based devcontainer.json** — use `image` instead
- **`onCreateCommand`/`updateContentCommand`/`initializeCommand`/`postAttachCommand`**
  — only `postCreateCommand` and `postStartCommand` run. `initializeCommand` in
  particular runs on the *host* in the real spec, before the container exists; `dvt`
  refuses it outright rather than running it in the wrong place.

A `devcontainer.json` using any of these is refused at `up` time — nothing is built
or run — rather than silently doing something different from what it asks for.
