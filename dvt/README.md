# dvt (devtemplate)

Dev-style named devcontainer templates, built and run directly via Docker or Podman.

Templates are fetched from [jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers)'s `templates/` directory.

## Install

    pipx install ./dvt

Requires network access to `github.com/jesserobertson/logerr` at install time (a
dependency not yet published to PyPI, pinned to a specific commit).

## Usage

    dvt feature sync
    dvt feature list
    dvt init ./my-project
    dvt feature add fastapi            # run from inside a project with .devcontainer/devcontainer.json
    dvt feature add agent
    dvt up my-project
    dvt ssh my-project

## Development

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
