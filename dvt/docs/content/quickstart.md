# Quickstart

This walkthrough scaffolds a new project, layers on the `fastapi` and `agent` features, and
starts it in a real container, built and run directly via Docker or Podman.

## 1. Sync features

Features are fetched from this repo's `templates/` directory on GitHub:

```bash
dvt feature sync
```

```
Synced 12 features: agent, cli, fastapi, huggingface, jax, marimo, mojo, ollama,
py-devtools, pytorch, rapids, transformers
```

## 2. See what's available

```bash
dvt feature list
```

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
project directory:

```bash
dvt up my-api
```

`dvt` pulls each added Feature, builds a multi-stage image from it, runs the container,
and runs `postCreateCommand` (then `postStartCommand`, if a feature sets one).

## 6. Connect

```bash
dvt ssh my-api
```

## 7. Remove a feature

```bash
dvt feature remove agent
```

Restores the fields `agent` touched, leaving everything else in `devcontainer.json` —
including `fastapi`'s own contribution and any manual edits — untouched.

## 8. Stop or remove the workspace

```bash
dvt stop my-api
dvt delete my-api
```
