# Quickstart

This walkthrough scaffolds a new project, layers on the `fastapi` and `agent` features, and
starts it in a real container, built and run directly via Docker or Podman.

## 1. Sync

Features and images are fetched from this repo's `templates/` and `images/`
directories on GitHub:

```bash
dvt sync
```

```
Synced 12 features: agent, cli, fastapi, huggingface, jax, marimo, mojo, ollama,
py-devtools, pytorch, rapids, transformers
Synced 2 images: base-cuda, base-ubuntu
```

## 2. See what's available

```bash
dvt feature list
```

The "Pulls in" column shows what each feature drags in transitively via
`dependsOn`. It reads a local cache, so run `dvt sync` once after upgrading dvt
for that column to populate. To inspect one feature's dependency graph on its
own:

```bash
dvt feature deps fastapi
```

`--format dot` / `--format mermaid` emit the graph for Graphviz or Mermaid
instead of the default tree, and `--json` gives
`{"<feature>": {"pulls_in": [...], "installs_after": [...]}}`. Omit the name to
cover the whole fleet. `dvt` doesn't inject these implied features into
`devcontainer.json` — its builder resolves `dependsOn` at image-build time; this
view only surfaces it.

## 3. Scaffold a project

```bash
dvt init ./my-api
```

This writes `./my-api/.devcontainer/devcontainer.json` with a default base image
(`ghcr.io/jesserobertson/base-ubuntu:latest` — override with `--image`) and `name` set to
the target directory's own name (`my-api`). No features are added yet.

## 4. Add features

```bash
cd my-api
dvt feature add fastapi
dvt feature add agent
```

Each `add` merges that feature's requirements (its own `features` entry, `runArgs`,
`postStartCommand`, etc.) into the existing `devcontainer.json` — see
[Concepts](concepts.md) for exactly how the merge works. If merging would produce an invalid
`devcontainer.json`, `add` refuses to write and leaves the file untouched.
`pytorch`/`rapids`/`jax`/`mojo`/`transformers` also override the base image to
`ghcr.io/jesserobertson/base-cuda:latest` when added, since they need a GPU.

## 5. Start the container

`up`'s `<name>` is the tag given to the workspace, not a path — run it from inside the
project directory. It's optional too: omit it and `up` uses the current directory's own name
(`my-api` here) the first time, or reuses whatever workspace is already tied to this folder on
a later run:

```bash
dvt up
```

`dvt` pulls each added Feature, builds a multi-stage image from it, runs the container,
and runs `postCreateCommand` (then `postStartCommand`, if a feature sets one).

## 6. Check on it

```bash
dvt info
```

Shows the project's image and applied features, plus — since it's running — its live status:
name, running/stopped state, and container name. `<name>` isn't needed here either; `info`
always operates on the current directory.

## 7. Connect

```bash
dvt ssh
```

Same story: no name needed from inside the project directory. (`dvt ssh my-api` still works
too, from anywhere.)

## 8. Remove a feature

```bash
dvt feature remove agent
```

Restores the fields `agent` touched, leaving everything else in `devcontainer.json` —
including `fastapi`'s own contribution and any manual edits — untouched.

## 9. Stop or remove the workspace

```bash
dvt stop
dvt delete
```
