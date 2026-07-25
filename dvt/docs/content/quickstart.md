# Quickstart

This walkthrough scaffolds a new project from the `fastapi` template, layers on the `agent`
feature, and starts it in a real container, built and run directly via Docker or Podman.

## 1. Sync templates

Templates are fetched from this repo's `templates/` directory on GitHub:

```bash
dvt template sync
```

```
Synced 12 templates: agent, cli, fastapi, huggingface, jax, marimo, mojo, ollama,
py-devtools, pytorch, rapids, transformers
```

## 2. See what's available

```bash
dvt template list
```

## 3. Scaffold a project

```bash
dvt project init --template fastapi ./my-api
```

This writes `./my-api/.devcontainer/devcontainer.json`, with `name` set to the target
directory's own name (`my-api`), not the template's.

## 4. Layer on another feature

```bash
cd my-api
dvt project add-feature agent
```

This merges the `agent` feature's requirements (its own `features` entry, `runArgs`,
`postStartCommand`, etc.) into the existing `devcontainer.json` — see
[Concepts](concepts.md) for exactly how the merge works. If merging would produce an invalid
`devcontainer.json`, `add-feature` refuses to write and leaves the file untouched.

## 5. Start the container

`up`'s `<name>` is the tag given to the workspace, not a path — run it from inside the
project directory:

```bash
dvt up my-api
```

`dvt` pulls the `agent` Feature, builds a multi-stage image from it, runs the container,
and runs `postCreateCommand` (then `postStartCommand`, if the template sets one).

## 6. Connect

```bash
dvt ssh my-api
```

## 7. Stop or remove it

```bash
dvt stop my-api
dvt delete my-api
```
