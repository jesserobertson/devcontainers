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

    pixi install
    pixi run pytest
