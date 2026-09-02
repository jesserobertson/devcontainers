# dvt (devtemplate)

Dev-style named devcontainer templates, built and run directly via Docker or Podman.

Templates are fetched from [jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers)'s `templates/` directory.

## Install

    pipx install dvt-cli

For a local checkout instead (e.g. to track `main`), use `pipx install ./dvt` from the
repo root.

## Usage

    dvt sync
    dvt feature list
    dvt init ./my-project
    dvt feature add fastapi            # run from inside a project with .devcontainer/devcontainer.json
    dvt feature add agent
    dvt up my-project
    dvt ssh my-project
    dvt run -n my-project pytest -q  # run one command inside the workspace, exit with its status
    dvt forward -n my-project 2718   # reach an in-container :2718 server at http://localhost:2718
    dvt info                        # from inside my-project - no name needed

## Reaching a server inside a workspace

A workspace's own network isn't routable from the host, so a server you start
inside one (a dev server, a notebook) isn't reachable at `localhost` until you
forward its port.

**Dynamically (no rebuild).** `dvt forward` tunnels one or more ports over the
same transport `dvt ssh` uses, until you Ctrl-C it:

    dvt forward -n my-project 2718            # localhost:2718 -> container 127.0.0.1:2718
    dvt forward -n my-project 8080:3000       # localhost:8080 -> container 127.0.0.1:3000
    dvt forward -n my-project 9000:db:5432    # localhost:9000 -> db:5432 from inside

Or bind the tunnel to the lifetime of a command:

    dvt run -n my-project -L 2718 just viz-notebooks   # marimo edit --port 2718, reachable while it runs
    dvt ssh my-project -L 8888                          # tunnel stays up for the shell session

The workspace image needs one of `socat`, `ncat`, `nc`, or `python3` on `PATH`
(the relay runs inside the container). `-L`/`--forward` is repeatable.

**Declaratively (at `dvt up`).** `appPort` and `forwardPorts` in
`devcontainer.json` are published to the host when the container is created:

```json
{ "image": "...", "appPort": [2718] }
```

Because published ports are fixed at creation, changing them makes the next
`dvt up` stop and ask for `dvt up --rebuild` rather than silently recreating
the container.

## Development

`dvt` is developed inside the [jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers)
monorepo, at `dvt/`. Clone that repo and run the commands below from `dvt/`.

The pixi `default` environment (what `pixi run` uses) carries `pytest` and
`hypothesis` for the dev loop. A separate `runtime` environment
(`pixi run -e runtime ...`) has none of that test tooling, for anyone who
wants to confirm the package installs cleanly without it — actual
distribution to end users is via `pipx install`, not pixi, so this is a
verification aid rather than the real install path.

    pixi install
    pixi run pytest

### Tasks

Each task below is a `pixi run` wrapper script; the ones with sub-commands
dispatch on the first extra argument (e.g. `pixi run test unit`), so `pixi
run <task>` alone isn't a valid invocation for those. `pixi <task>` (without
`run`) never works — `pixi` requires the `run` verb to invoke a task.

| Task | Sub-commands | What it does |
|---|---|---|
| `pixi run quality` | `check`, `typecheck`, `lint`, `format [--check]`, `fix`, `coverage [--html]` | mypy + ruff lint/format, or auto-fix both with `fix` |
| `pixi run test` | `unit`, `integration`, `all`, `fast`, `clean` | pytest by tier — `integration` needs a real Docker/Podman runtime and isn't included in `all`/`fast` |
| `pixi run docs` | `serve [--port]`, `build [--strict]`, `status`, `clean` | mkdocs site under `docs/` — `serve` runs a local dev server at `localhost:8000` |
| `pixi run check-all` | — | `test all` + `quality check`, for a full pre-push sanity check |
| `pixi run clean` | — | `test clean` + `docs clean` |
