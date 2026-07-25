# dvt Real SSH + Podman-Windows Machine Lifecycle — Design

## Purpose

The native container runtime work (`docs/superpowers/plans/2026-07-25-dvt-native-container-runtime.md`)
shipped with two known gaps, both surfaced by that plan's own final review:

1. **`dvt ssh --stdio`'s target doesn't speak SSH.** The `~/.ssh/config` `ProxyCommand`
   integration was removed entirely because piping to a bare `docker exec ... sh`
   can't participate in real SSH protocol negotiation — `ProxyCommand` only replaces
   the transport, not the protocol. `dvt ssh <name>` (direct exec) works; plain
   `ssh <name>`, VS Code Remote-SSH, and JetBrains Gateway do not.
2. **Podman-on-Windows was never implemented**, only stubbed: `runtime.py`'s
   `_default_podman_socket()` returns `None` on `win32` unconditionally, since
   resolving a Podman connection endpoint on Windows requires managing a
   `podman machine` (a WSL2-backed VM), not just guessing a socket path.

This phase closes both, plus two smaller items: Feature refs pinned by digest
(`@sha256:...`) rather than tag, and pulling GPU support on Podman-Windows into
parity with Docker (5 of the 12 templates in this repo use `--gpus all`).

The Podman-Windows machine-lifecycle design is ported directly from
`github.com/jesserobertson/devpod-podman-powershell` (a `devpod` provider the
user already wrote and runs) — its `scripts/init.ps1` is the reference
implementation for exactly this problem, already solving machine detection,
auto-start, and NVIDIA CDI setup via `podman machine`'s own JSON-output
subcommands. This design ports that logic into Python rather than reusing it
as a devpod provider (dvt has no devpod dependency to plug a provider into).

## Real SSH Support

### Why the previous design failed

`ProxyCommand <cmd>` tells the local `ssh` client "pipe your SSH protocol
bytes through this command's stdin/stdout instead of opening a TCP socket."
The client still performs the full SSH handshake (version banner exchange, key
exchange, service request, auth, channel open) over that pipe. Piping to a
bare shell means the shell receives the client's SSH version-string banner as
literal input — the shell has no reply, no key exchange, no channel semantics
— and the client hangs or errors out. `devpod` avoids this because it runs its
own lightweight SSH-speaking agent inside the container; the ProxyCommand
shape only works when something on the other end actually speaks SSH.

### Fix: a real (if minimal) SSH server

`dvt ssh --stdio <name>` runs an `asyncssh` server bound to this process's own
stdin/stdout (not a network socket), authenticates trivially (see below), and
bridges each opened session channel to a `docker`/`podman exec -i` subprocess
— the exact same exec-plumbing approach `exec_interactive` already uses and
has been reviewed three times over. `dvt ssh <name>` typed directly is
**unaffected** — it stays exactly as `exec_interactive` today, no SSH protocol
involved, since it already works correctly.

**No real authentication.** The server only ever listens over the stdio pipe a
`dvt ssh --stdio <name>` subprocess owns — never a real network socket.
Whoever can spawn that subprocess already has local shell access to this
machine (the actual security boundary; the same trust model `docker exec`
itself has via socket/group permissions). Requiring SSH-level auth on top
would check a credential that adds no real security, only ceremony. The
server's `begin_auth` returns `False` (no auth required) for any username.

**Ephemeral host key**, generated fresh (in memory, `asyncssh.generate_private_key`)
on every `--stdio` invocation — no persistence, no key storage. This is safe
specifically because the written `~/.ssh/config` entry sets
`StrictHostKeyChecking no` / `UserKnownHostsFile /dev/null` (unchanged from the
original Task 5 design), so host identity is never actually verified
client-side; persisting a host key across invocations would add file-management
complexity for zero verification benefit.

**PTY handling, v1 scope.** `asyncssh`'s `pty_requested` callback fires when a
client asks for a terminal (every interactive `ssh`/Remote-SSH/Gateway session
does). v1 responds by spawning the bridged `docker exec -it` subprocess with
pipe-connected stdin/stdout (not this process's own terminal — `asyncssh` owns
the actual terminal now) and forwards the initial terminal size once at
session start. **Known gap, not blocking:** dynamic window-resize forwarding
(SIGWINCH → asyncssh's `terminal_size_changed` → re-signal the exec'd process)
is not implemented in v1; a client resizing their terminal mid-session won't
propagate. This mirrors how Phase 1 deferred non-blocking gaps rather than
scope-creeping the core deliverable.

**`write_ssh_config_entry`/`remove_ssh_config_entry` return** to `ssh.py`,
essentially unchanged from the original (now-removed) Task 5 code — the
`ProxyCommand dvt ssh --stdio <name>` shape was always structurally correct;
only the far end needed to actually speak SSH.

## Podman-on-Windows Machine Lifecycle

Ported from `devpod-podman-powershell`'s `init.ps1`, in Python, shelling out to
the `podman` CLI (already required to be on `PATH` for `RuntimeHandle.cli_binary`
resolution) rather than the daemon socket directly, since machine lifecycle
operations (`machine list/inspect/init/start/set/ssh`) are CLI-only — no REST
API for them:

1. `podman machine list --format json` → auto-detect a machine name (or none).
2. If none found: **do not auto-create one** (default off, matching the
   reference provider's own `PODMAN_MACHINE_AUTO_INIT=false` default) — refuse
   with a clear error naming the manual fix (`podman machine init`).
3. If found: `podman machine inspect <name> --format json` → check `State`.
   - Not `running`: **auto-start** (default on, matching the reference
     provider's `PODMAN_MACHINE_AUTO_START=true` default), poll `podman ps`
     until it succeeds or a timeout elapses.
   - Already running: proceed directly.
4. Resolve the actual Docker-API-compatible connection endpoint from
   `podman machine inspect`'s own output — verified directly against a real
   machine on this project's own Windows host (`podman machine inspect
   devpod-machine`):
   ```json
   "ConnectionInfo": {
     "PodmanSocket": { "Path": "C:\\Users\\...\\Temp\\podman\\devpod-machine-api.sock" },
     "PodmanPipe": { "Path": "\\\\.\\pipe\\podman-devpod-machine" }
   }
   ```
   On Windows, `ConnectionInfo.PodmanPipe.Path` is the value to use, translated
   into docker-py's expected `npipe:////./pipe/<name>` URL form (docker-py's
   own `base_url` parameter). `runtime.py` never needs to guess a path the way
   `_default_podman_socket()`'s Linux/macOS rootless-socket fallback does today
   — this makes Windows strictly more reliable than the existing Unix fallback,
   since it reads the real value rather than assuming a default location.
5. **GPU templates** (`runArgs` containing `["--gpus", "all"]`, translated by
   `container.py`'s existing `_translate_run_args`): before `run_container`,
   check `podman machine ssh <name> "test -f /etc/cdi/nvidia.yaml"`; if
   missing, install the NVIDIA Container Toolkit and generate CDI specs via
   the same commands `init.ps1` runs, over `podman machine ssh`. This mirrors
   the reference provider exactly — without it, GPU templates simply fail on
   Podman-Windows even with a real GPU and driver present, since the toolkit
   is the actual missing piece, not anything `dvt` itself builds or runs.

This logic only activates on `win32` when the resolved engine is Podman —
Linux/macOS Podman (already handled via the existing rootless-socket-path
fallback) and Docker on any platform are unaffected.

**Settings additions**, minimal (not the reference provider's full ~9-knob
surface — scope this down to what's needed for auto-detection to work,
matching the "don't build for hypothetical future requirements" principle):
`podman_machine_auto_init: bool = False`, `podman_machine_auto_start: bool = True`,
both `DVT_`-prefixed env vars per the existing `Settings` convention. CPU/memory/
disk-size defaults for the (rare, opt-in) auto-init case are hardcoded matching
the reference provider's own defaults (2 CPUs / 4096MB / 100GB), not exposed as
settings in v1.

## Digest-Pinned Feature Refs

`features.py`'s `_parse_feature_ref` currently requires a `:tag` suffix. OCI
registries accept a manifest reference that's either a tag or a
`sha256:<hex>` digest in the same URL position
(`/v2/{repository}/manifests/{reference}`), so this is purely a parsing
extension: recognize `ref@sha256:<hex>` (the standard OCI digest-ref syntax)
alongside `ref:tag`, and use whichever was given directly as the manifest URL
segment — no change to the fetch/auth/extract logic at all.

## JetBrains Gateway

No new design. Once real SSH exists, this is a manual verification task: does
Gateway actually connect through the `ProxyCommand` entry `dvt up` writes.
Tracked as a plan task with a manual verification checklist, not new code.

## Testing Strategy

- **Unit tests** (mocked): `asyncssh` server auth/pty-callback wiring (mock the
  channel, assert the bridged subprocess is spawned with the right argv and
  pipes), the Podman-Windows machine-lifecycle state machine (mock `subprocess.run`
  returning canned `podman machine list/inspect` JSON for each state: no
  machine, stopped, running, running-with-GPU-CDI-missing), digest-ref parsing.
- **Integration tests** (real, opt-in, `@pytest.mark.integration`, following
  the pattern already established in `test_native_runtime_lifecycle.py`): a
  real `ssh <name>` (the actual OpenSSH client binary, not `dvt ssh`) through
  the ProxyCommand entry, proving the protocol negotiation genuinely works —
  this is the test that would have caught the original bug, so it must exist
  before this phase is considered done. Skipped cleanly if `ssh`/`sshd`-capable
  tooling or a runtime isn't available, exactly like the existing integration
  test's skip condition.
