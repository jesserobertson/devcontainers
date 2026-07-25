# dvt Native Container Runtime — Design

**Amended after implementation and final review:** the SSH section below describes
a `ProxyCommand`-over-`docker exec` shim that turned out to be fundamentally broken
— `ProxyCommand` only replaces the transport a real `ssh` client uses; the client
still performs actual SSH protocol negotiation over that pipe, which a bare shell
can't participate in. This was incorrectly believed to mirror `devpod`'s own
approach; `devpod` actually works because it runs its own SSH-speaking agent inside
the container, which this design's explicit "no sshd" choice never provides. The
`~/.ssh/config` integration was removed entirely as a result — `dvt ssh <name>`
(direct `docker`/`podman exec -it`, no real SSH protocol involved) is the only
supported terminal-access path. See the implementation plan's own amendment note
for the full account. The SSH section below is kept as a historical record of the
(incorrect) original design, not as current guidance.

## Purpose

`dvt` currently implements `up`/`ssh`/`stop`/`delete` as a thin passthrough to the
`devpod` CLI binary (`devtemplate/cli.py::_run_devpod`). This spec replaces that
passthrough with a native implementation built directly on `docker-py`, talking to
either a Docker or a Podman engine, removing the `devpod` dependency entirely.

The driving goal is **compatibility, not parity**: containers `dvt` builds and runs
should be recognizable and usable by other devcontainer-aware tooling (VS Code's Dev
Containers extension, the official `@devcontainers/cli`, `devpod` itself) via standard
container labels and mount conventions — without `dvt` reimplementing the full
devcontainer specification (no docker-compose support, no Feature dependency
ordering, no build-time `initializeCommand`/`onCreateCommand`/`updateContentCommand`/
`postAttachCommand`).

This scope was validated directly against this repo's own `templates/` and
`features/`: all 12 templates are image-only (no `build.dockerfile`, no compose),
each references exactly one Feature, no Feature declares `installsAfter`/
`dependsOn`, and only `ollama`'s Feature declares real `options` (currently always
used at their defaults). Features matter enough to include in v1 — every template
uses one — but the dependency-ordering and multi-Feature-per-template machinery the
full spec allows for does not need to exist yet.

## Non-Goals (v1)

- **docker-compose devcontainers.** Not used by any current template.
- **Feature dependency ordering** (`installsAfter`/`dependsOn`). Every template
  uses exactly one Feature; no ordering logic is needed.
- **`build.dockerfile`-based devcontainer.json.** All current templates are
  `image`-only.
- **`onCreateCommand`/`updateContentCommand`/`initializeCommand`/`postAttachCommand`.**
  Only `postCreateCommand` and `postStartCommand` are used today.
  `initializeCommand` in particular runs on the **host**, before the container
  exists — a build-time or exec-time implementation would run it in the wrong
  place entirely, so it's refused rather than mishandled.
- **Authenticated/private registry pulls.** Anonymous pull only; base images and
  Features referenced from any public OCI registry work, private ones fail with a
  clear error.
- **Real sshd inside containers.** SSH is a `ProxyCommand` shim over `docker exec`
  (see below) — no SSH server, no published port, no image changes required for
  SSH to work.

A `devcontainer.json` that uses any non-goal field is a **refusal at parse time**
(nothing built, nothing run), matching the existing "validate first, refuse rather
than guess" philosophy already used by `store.py`'s template-name validation and
`project.py`'s schema-validate-before-write.

## Architecture

New modules under `src/devtemplate/`:

- **`runtime.py`** — resolves a `docker-py` `DockerClient` against Docker's
  endpoint, Podman's Docker-API-compatible socket, or a forced choice. A single
  `docker-py` client works against both engines since Podman speaks the same API —
  no separate `podman-py` dependency needed. Selection is governed by a new
  `Settings.runtime: Literal["auto", "docker", "podman"] = "auto"` field
  (env var `DVT_RUNTIME`, matching the existing `DVT_`-prefixed settings). `"auto"`
  tries Docker's endpoint first, then Podman's compatible socket, failing with a
  clear "no container runtime found" error if neither responds.

- **`features.py`** — OCI artifact pull for Features: given a ref like
  `ghcr.io/jesserobertson/devcontainers/fastapi:latest`, fetch the manifest, fetch
  the blob(s), extract the tarball into a cache dir under `Settings.data_dir`
  (alongside the existing template cache). This needs a generic OCI registry client
  (manifest + blob fetch over the registry HTTP API) because Features are a
  generic-artifact media type, not a runnable container image — `docker-py`'s own
  `client.images.pull()` handles base *images* directly and needs no new code.

- **`build.py`** — generates a multi-stage Dockerfile: `FROM <base image>`, then one
  stage per Feature that `COPY`s in the extracted Feature dir and runs its
  `install.sh` with the Feature's resolved option values as environment variables
  (uppercased option id, e.g. `host` → `HOST`), plus the spec's standard
  `_REMOTE_USER`/`_CONTAINER_USER`/`_CONTAINER_USER_HOME` env vars so well-behaved
  third-party Features work even though this repo's own `install.sh` scripts
  currently hardcode `dev` rather than reading them. Builds via `client.images.build()`,
  producing a normal, standalone, taggable image — usable by anything with a Docker
  or Podman client, not just `dvt`.

- **`container.py`** — translates `devcontainer.json`'s `mounts`/`runArgs`/
  `remoteUser`/`containerEnv` into `docker-py` run kwargs (defaulting
  `workspaceFolder`/`workspaceMount` to the spec's `/workspaces/<folder-name>`
  convention when a template omits them, since not every third-party
  `devcontainer.json` will set them explicitly the way this repo's templates do).
  Computes the compatibility labels:
  - `devcontainer.metadata` — base64-encoded JSON of the merged config, the
    convention VS Code and other tooling use to recognize and introspect a
    devcontainer
  - `devcontainer.local_folder` — absolute path of the project directory
  - `devcontainer.config_file` — path to the `devcontainer.json` used
  - `dvt.workspace` — the user-supplied `<name>`, the lookup key `ssh`/`stop`/
    `delete` filter on

  Runs `postCreateCommand` then `postStartCommand` via `exec_run()` after the
  container starts.

- **`ssh.py`** — on successful `up`, writes/updates a `~/.ssh/config` `Host <name>`
  entry whose `ProxyCommand` is `dvt ssh --stdio <name>`. That `--stdio` mode pipes
  stdin/stdout directly into `docker exec -i <container> <shell>` — no sshd, no
  published port, no host keys, nothing baked into any image. This is the same
  approach `devpod` itself uses under the hood. Plain `ssh <name>`, VS Code
  Remote-SSH, and JetBrains Gateway all work transparently through this, since none
  of them care whether the far end of the `ProxyCommand` pipe is a real network
  socket. `delete` removes the config entry along with the container.

## CLI Changes

`up`/`ssh`/`stop`/`delete` in `cli.py` are rewired onto the modules above; all
`devpod`-specific code (`_run_devpod`, `_devpod_passthrough`, the
`shutil.which("devpod")` resolution) is deleted, along with devpod mentions in
`docs/content/`.

- **`dvt up <name>`** operates on cwd's `.devcontainer/devcontainer.json`, matching
  `project add-feature`'s existing cwd-relative convention — `<name>` is the tag
  given to the resulting container, not a path. Flow: refuse if the config uses any
  non-goal field → resolve the runtime client → pull each Feature → build the image
  → run the container with labels → run `postCreateCommand` then
  `postStartCommand` → write the SSH config entry.
- **`dvt ssh <name>`** (interactive, typed directly) execs straight into the
  container via `docker exec -it`. **`dvt ssh --stdio <name>`** is the non-interactive
  pipe mode the `ProxyCommand` entry invokes.
- **`dvt stop <name>`** / **`dvt delete <name>`** filter containers by
  `label=dvt.workspace=<name>` and act on whatever's found — no separate `dvt`-side
  workspace registry. This means Docker/Podman itself is the single source of
  truth: nothing to keep in sync, nothing that can drift if a container is removed
  outside `dvt` (`docker rm`, `docker system prune`). `delete` removes the
  container and its SSH config entry, but leaves the built image cached (same
  spirit as `devpod`'s own delete: tear down the workspace, keep the image for next
  time).

All four commands work from any directory once a workspace exists, exactly like
the current `devpod`-backed versions do today.

## Error Handling

Follows the existing codebase convention throughout: every fallible function
returns `Result[T, Exception]`, unwrapped at the CLI boundary via the
`unwrap_or_exit()` helper already introduced in `cli_support.py`. Where a step
processes a list uniformly (e.g. pulling every Feature a config references),
`logerr.itertools.traverse_result` short-circuits on the first failure rather than
a hand-rolled loop, following the pattern already adopted in `store.py`.

## Testing Strategy

Follows the pattern already proven for the `devpod` lifecycle test
(`tests/integration/test_devpod_lifecycle.py`):

- **Unit tests** (mocked): Dockerfile generation from a given base image + Feature
  set, label computation, option → env var mapping, devcontainer.json refusal
  logic for non-goal fields, SSH config entry read/write. The OCI manifest/blob
  fetch in `features.py` is tested against a mocked transport, the same way
  `github.py` is tested today (`httpx.MockTransport`).
- **Integration tests** (real, opt-in, `@pytest.mark.integration`): a full
  `up` → `ssh --stdio` (or direct `exec`) → `stop` → `delete` cycle against a real
  Docker (or Podman) daemon, skipped via `skipif` when no runtime is reachable —
  mirroring `test_devpod_lifecycle.py`'s existing skip condition, generalized from
  "devpod on PATH" to "a runtime resolves via `runtime.py`".

## Known Gaps / Follow-ups (not blocking v1)

- Feature refs pinned by digest (`@sha256:...`) rather than tag are not handled by
  the OCI puller in v1 — only tag refs are resolved.
- Podman-on-Windows (WSL2-backed `podman machine`) and the auto-detect fallback
  path haven't been exercised on this project's CI matrix yet; worth a real
  smoke test the way `devpod`'s lifecycle was smoke-tested for this repo's initial
  build.
- JetBrains Gateway's compatibility with the `ProxyCommand` shim is assumed by
  analogy with `devpod`'s own approach, not independently verified.
