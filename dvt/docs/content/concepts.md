# Concepts

## The merge algorithm

`dvt project add-feature` layers a new feature's template onto an existing project's
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

## Why `name`/`workspaceFolder`/`workspaceMount` are stripped first

Before merging, `add-feature` removes `name`, `workspaceFolder`, and `workspaceMount` from
the incoming feature template. Every template in this repo sets its own `name` to its own
feature name — `templates/agent/devcontainer.json` literally has `"name": "agent"`. Applying
the merge rule above unfiltered would silently rename your project to whatever feature you
just added. `workspaceFolder`/`workspaceMount` are identical across every template anyway, so
stripping them costs nothing.

## Schema validation on write

Before writing, both `add-feature` and `project init` validate the result against a vendored
copy of the official [devcontainer.json base
schema](https://github.com/devcontainers/spec/blob/main/schemas/devContainer.base.schema.json).
If validation fails, nothing is written — the target file is left exactly as it was. This
matters because `DVT_GITHUB_REPO` is user-overridable: a malicious or just-broken fork's
templates get caught before they ever touch your project.

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
