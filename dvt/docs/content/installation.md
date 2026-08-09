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

## Podman on Windows

`dvt` talks to Podman's own Windows machine (a WSL VM) directly — no manual `podman
machine start` is required for normal use:

- **Auto-start** (default on): if the machine exists but is stopped, `up`/`ssh`/`stop`/
  `delete` start it automatically. Disable with `DVT_PODMAN_MACHINE_AUTO_START=false`,
  which turns a stopped machine into a clean `Err` instead.
- **Auto-init** (default off): if no machine exists at all, `dvt` refuses rather than
  silently running the multi-minute `podman machine init` download/unpack for you. Opt
  in with `DVT_PODMAN_MACHINE_AUTO_INIT=true`.

Both operations can take several minutes; `dvt` prints a progress message to stderr
before starting either one, so it never looks like a silent hang.

### GPU / NVIDIA CDI

If a devcontainer.json's `runArgs` includes `--gpus all`, `dvt up` checks the Podman
machine for CDI (Container Device Interface) readiness and, if needed, installs the
NVIDIA Container Toolkit and generates the CDI spec inside the machine automatically
before building. Requires an NVIDIA GPU passed through to WSL2.
