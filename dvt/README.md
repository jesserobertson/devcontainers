# dvt (devtemplate)

Dev-style named devcontainer templates on top of [DevPod](https://devpod.sh).

Templates are fetched from [jesserobertson/devcontainers](https://github.com/jesserobertson/devcontainers)'s `templates/` directory.

## Install

    pipx install ./dvt

## Usage

    dvt template sync
    dvt template list
    dvt project init --template fastapi ./my-project
    dvt project add-feature agent      # run from inside a project with .devcontainer/devcontainer.json
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
