# Installation

## Requirements

- Python 3.12 or 3.13 (`pyproject.toml` currently caps at `<3.15`)
- [`pipx`](https://pipx.pypa.io/) (recommended) or `pip`
- [Docker](https://www.docker.com/) or [Podman](https://podman.io/) — only needed for the
  `up`/`ssh`/`stop`/`delete` commands. `template`/`project` commands work without either.

## Install

```bash
pipx install ./dvt
```

`logerr`, one of `dvt`'s dependencies, isn't published to PyPI yet — it's pinned as a git
dependency, so installation requires network access to `github.com/jesserobertson/logerr` in
addition to PyPI.

## Verify

```bash
dvt --help
```

## Upgrading

Since `dvt` isn't published to PyPI, reinstall from an updated checkout to upgrade:

```bash
git -C /path/to/devcontainers pull
pipx install --force /path/to/devcontainers/dvt
```
