# dvt host↔container port forwarding — design

Status: approved (brainstorm), 2026-09-03
Author: Jess Robertson (with Claude)

## Problem

`dvt` manages named devcontainer workspaces via Podman/Docker (`dvt up`,
`dvt run`, `dvt ssh`, `dvt stop`). There is no way to reach a server running
inside a workspace from the host browser. Concretely, `dvt run just
viz-notebooks` starts `marimo edit --host 0.0.0.0 --port 2718` inside the
container; it binds fine, but from the host the page is unreachable because:

- `dvt up` creates the container with no published ports (`podman inspect`
  shows `PortBindings: {}`);
- `devcontainer.json` `forwardPorts` / `appPort` are ignored by `dvt`;
- the container IP (e.g. `10.88.0.7`) is on the internal Podman bridge, not
  routable from a Windows/macOS host;
- `dvt run` is a plain `exec` into the already-running container, so it can't
  publish anything.

`dvt ssh` does work: `dvt` writes an `~/.ssh/config` block per workspace
(`Host <name>` with `ProxyCommand dvt ssh --stdio <name>`), so
`ssh -N -L 2718:localhost:2718 <name>` is a working manual workaround today —
but undocumented and unautomated.

## Goals

1. **Dynamic path (no container recreate)** — a `dvt forward <spec>` command
   and repeatable `--forward` / `-L <spec>` flags on `dvt run` and `dvt ssh`,
   where `<spec>` is `LOCAL[:REMOTE_HOST:]REMOTE` (default
   `REMOTE_HOST=localhost`, `LOCAL=REMOTE`). Multiple specs supported. Tunnel
   torn down cleanly on `SIGINT`. Must **not** require `dvt up --rebuild`, and
   must not assume host networking (works on Windows/macOS).
2. **Declarative path** — honor `appPort` (hard publish) and `forwardPorts`
   from `devcontainer.json` at `dvt up` time by passing published ports to
   `podman`/`docker run`. Since the port set must be known at create time,
   detect when a running container's published ports no longer match the
   config and tell the user to `dvt up --rebuild` (don't silently recreate).

## Non-goals (v1)

- Reverse (`-R`) forwarding; dynamic (`-D` / SOCKS) forwarding.
- `0`-port auto-allocation.
- A backgrounded/daemonized `dvt forward` with a separate stop command
  (`dvt forward` is foreground only).
- IPv6-literal remote hosts.
- Auto-reforwarding across a container restart.

## Key architectural finding

The `dvt ssh --stdio` transport is **an `asyncssh` server that runs on the
host** (in the `dvt ssh --stdio` subprocess), bridging *session* channels to
`podman`/`docker exec` against the container. It is **not** an `sshd` inside
the container.

Consequences:

- `asyncssh`'s `SSHServer.connection_requested` rejects `direct-tcpip`
  (local-forward) channels by default; even if enabled, `asyncssh`'s
  "standard port forwarding" opens the outbound connection **from the server
  process, i.e. the host** — not from inside the container. So a naive
  `ssh -L` over the existing `ProxyCommand` cannot reach the container's
  `localhost`.
- Therefore *any* implementation must spawn a small byte-relay **inside the
  container** (`exec -i` + `socat`/`nc`/`python3`) per forwarded connection.
  Given that this in-container relay is unavoidable either way, a
  self-contained Python forwarder is simpler than teaching the `asyncssh`
  server to bridge `direct-tcpip` channels, needs no `ssh` binary on the
  host, and gives one code path shared by `dvt forward`, `dvt run -L`, and
  `dvt ssh -L`.

Decision (confirmed in brainstorming): **self-contained Python forwarder**,
stdlib only, no new dependencies.

## Design

### New module: `src/devtemplate/forward.py`

Reuses the raw-fd pump discipline already established in
`src/devtemplate/sshd/stdio.py` (read/write real fds with `os.read`/
`os.write`, return on first-available bytes, loop short writes — do **not**
use buffered wrappers; see that module's docstring for the full rationale).

#### `ForwardSpec`

Frozen dataclass: `bind: str`, `local: int`, `remote_host: str`,
`remote: int`. Classmethod `parse(text: str) -> ForwardSpec` mirrors
`ssh -L`, plus a bare-port shorthand. Colon-split field counts:

| Input | bind | local | remote_host | remote |
|---|---|---|---|---|
| `2718` | `127.0.0.1` | 2718 | `localhost` | 2718 |
| `8080:3000` | `127.0.0.1` | 8080 | `localhost` | 3000 |
| `9000:db:5432` | `127.0.0.1` | 9000 | `db` | 5432 |
| `0.0.0.0:8080:db:5432` | `0.0.0.0` | 8080 | `db` | 5432 |

`__str__` renders the canonical `bind:local:remote_host:remote`. Malformed
input (`""`, 5+ fields, non-numeric port, empty host) → `ValueError` naming
the offending string. Parser carries doctests (run by `--doctest-modules`).

#### Relay selection

Once per `PortForwarder`, probe the container:

```
sh -c 'command -v socat || command -v ncat || command -v nc || command -v python3'
```

First hit wins; relay argv per tool:

- `socat` → `socat - TCP:<remote_host>:<remote>`
- `ncat`  → `ncat <remote_host> <remote>`
- `nc`    → `nc <remote_host> <remote>` (plain form; BusyBox- and GNU-compatible)
- `python3` → `python3 -c '<inline relay>'` — connect a socket to
  `<remote_host>:<remote>`, then pump each direction on a thread with
  `shutil.copyfileobj`, half-close on EOF.

None found → hard `ValueError` before any listener is opened, naming all four
tools and pointing at the declarative route (`appPort` + `dvt up --rebuild`)
as the alternative that needs nothing installed in the image.

#### `PortForwarder`

Context manager constructed with `(cli_binary, container_name, specs)`.

- `__enter__`: for each spec, bind a host `socket` listener on
  `spec.bind:spec.local` (SO_REUSEADDR), start one acceptor daemon thread per
  listener. `OSError` on bind → `ValueError` naming the port and spec (e.g.
  "local port 2718 is already in use").
- Per accepted connection: `subprocess.Popen([cli_binary, "exec", "-i",
  container_name, "sh", "-c", relay_cmd], stdin=PIPE, stdout=PIPE)` and two
  daemon threads pumping `client_sock ⇄ proc.stdin/stdout` over raw fds.
  Close the socket and `proc` when either side hits EOF. A per-connection
  failure (relay tool missing at runtime, remote not yet listening) is logged
  to stderr via `loguru` and closes just that connection; the forwarder stays
  up.
- `close()` / `__exit__`: close listener sockets (unblocks the acceptors),
  `terminate()` any live relay children, join pump/acceptor threads with a
  short timeout (daemon threads, so a stuck join can't hang exit — same
  tradeoff as `devtemplate.net` / `devtemplate.pty.bridge`).

### Command surface

All three, sharing `PortForwarder`.

#### `dvt forward [-n NAME] SPEC...` (new command in `cli.py`)

- Workspace named with `-n/--name` (an option, following `dvt run`, not a
  positional — a positional name would be ambiguous against a bare-port
  spec like `dvt forward 2718`). Omitted → `resolve_existing(client, None,
  cwd, "forward")` infers from cwd like `ssh`/`run`.
- All positionals are specs; at least one required. Each is parsed with
  `ForwardSpec.parse`, so a malformed one fails before any listener opens.
- Foreground: prints each mapping
  (`127.0.0.1:2718 → <name>:localhost:2718`), then blocks.
- `KeyboardInterrupt` (SIGINT) → `PortForwarder.close()`, print
  `Stopped forwarding.`, exit 0.
- Setup failure (bad spec, port in use, no relay tool, workspace not running)
  → `unwrap_or_exit`, exit 1.
- No `--json` (long-running foreground, consistent with `dvt ssh`). No new
  output schema.

#### `dvt run -L/--forward SPEC` (repeatable)

Wrap the existing `exec_command(...)` call in `with PortForwarder(...)`. The
tunnel lives exactly as long as the run command — this is the
`dvt run -L 2718 just viz-notebooks` / marimo case. The command's own exit
code is still what `dvt run` returns. Options must precede the command, as
today (`context_settings={"ignore_unknown_options": True}` is unchanged).

#### `dvt ssh -L/--forward SPEC` (repeatable)

Wrap `exec_interactive(...)` in `with PortForwarder(...)`. Interactive
`dvt ssh` execs `docker exec` (not `ssh`), so the forwarder runs alongside
it. `--stdio` mode ignores `-L`.

### Declarative: `appPort` + `forwardPorts` at `dvt up`

#### `translate_published_ports(config) -> dict[str, tuple[str, int]]` (in `container.py`)

- `appPort`: `int`, `"host:container"`, or a list of those → publish each.
- `forwardPorts`: list of `int` or `"host:container"` → publish each. A
  non-numeric first segment (the spec's `"label:port"` editor sugar) →
  `ValueError` pointing at `-L` / `dvt forward`.
- Both absent/empty → `{}` (byte-for-byte today's behavior).
- Container-side bind defaults to `127.0.0.1` — `{"2718/tcp": ("127.0.0.1",
  2718)}` — matching the dynamic path and avoiding a surprise `0.0.0.0`
  exposure.

Wired into `run_container(...)` as `ports=translate_published_ports(config)`
on the `client.containers.run(...)` call. docker-py's `ports=` works against
both Podman (docker-compat API) and Docker. On macOS/Windows the publish
lands on the Docker Desktop / podman-machine VM, which forwards to host
loopback — no `--network=host`. Caveat (documented, not enforced): rootless
Podman can't publish ports <1024; dev servers use high ports.

#### Drift detection — already covered

`config_has_drifted` compares the whole `devcontainer.json` dict against the
container's `devcontainer.metadata` label, so editing `appPort` /
`forwardPorts` already makes `dvt up` refuse via `config_drift_error`, which
lists the changed keys and points at `dvt up --rebuild`. No second,
port-specific drift check is added (redundant surface). The design adds:

- a test that pins this path (`appPort` edit → `dvt up` refuses, output
  mentions `--rebuild`);
- a sentence in `config_has_drifted` / `config_drift_error` docstrings noting
  published ports are part of what's covered.

## Error handling summary

| Condition | Behavior |
|---|---|
| Workspace not running | `ValueError("No workspace named 'x' is running.")` (matches `ssh.py` phrasing) |
| `local` port already bound on host | `OSError` caught at bind → `ValueError` naming port + spec |
| No relay tool in container | hard `ValueError` before any listener opens, names all four tools + declarative alternative |
| Malformed spec | `ValueError` naming the offending string |
| `dvt forward` SIGINT | `close()`, print `Stopped forwarding.`, exit 0 |
| `dvt run -L` / `dvt ssh -L` unwind (normal exit, non-zero, Ctrl-C) | `with` block tears the tunnel down; command's exit code preserved |
| Per-connection relay failure | logged to stderr, that connection closed, forwarder stays up |

## Testing

### Unit — `tests/test_forward.py` (fully mocked)

- `ForwardSpec.parse`: table of the four forms + bare shorthand → expected
  fields; malformed inputs → `ValueError`. Hypothesis property:
  `parse(str(spec)) == spec` round-trip.
- Relay selection: fake `command -v` output with each tool present/absent in
  turn → expected relay argv; none present → error mentions all four tools.
- `PortForwarder` round-trip: `Popen` monkeypatched to a local echo stand-in;
  connect to the host listener, send bytes, assert they echo back; `close()`
  → child terminated, listener socket closed.
- Bind conflict: pre-bind the port, assert the port-named error.

### Unit — `tests/test_container.py` additions

- `translate_published_ports`: `appPort` as int / `"h:c"` / list;
  `forwardPorts` list of ints and `"h:c"`; both together; both absent → `{}`;
  non-numeric `forwardPorts` entry → `ValueError`.
- `run_container` forwards the mapping to `client.containers.run` (existing
  fake-client kwarg assertion).

### Integration — `tests/integration/test_port_forward.py`

`@pytest.mark.integration`, skips cleanly if no runtime. Mirrors
`test_native_runtime_lifecycle.py`.

1. **Dynamic**: throwaway workspace from `python:3.12-alpine`. `dvt run -n
   <ws> sh -c "(python3 -m http.server 2718 &) ; sleep 1"` to leave a
   listener in the container. Start a `PortForwarder` (or `dvt forward` on a
   thread) for `2718`; poll `http://127.0.0.1:2718/` with `http.client`
   until 200 or timeout; assert the body is the directory listing. `finally`:
   forwarder `close()`, `dvt delete`.
2. **Declarative**: `devcontainer.json` with `"appPort": [2718]` → `dvt up` →
   assert `inspect` `PortBindings` non-empty and a host GET succeeds. Rewrite
   to `"appPort": [2719]`, `dvt up` (no `--rebuild`) → assert non-zero exit
   and output contains `--rebuild`.

## Docs

- **README**: `dvt forward -n my-project 2718` in the usage block; new section
  *"Reaching a server inside a workspace"* covering `dvt forward`,
  `dvt run -L 2718 just viz-notebooks` (marimo / dev-server call-out),
  `dvt ssh -L`, and the declarative `appPort` / `forwardPorts` route with the
  `dvt up --rebuild` note.
- `--describe` / `--help`: help strings on the new command and the `-L`
  options (manifest pickup is automatic via `describe.Typer`).
- **CHANGELOG.md**: `Added` entry under a new `Unreleased` section.
- Module docstring on `forward.py` recording the host-terminated-SSH /
  in-container-relay rationale.

## Files touched

- `src/devtemplate/forward.py` — new.
- `src/devtemplate/cli.py` — `forward` command; `-L` option on `run` and
  `ssh`; wrap the exec calls.
- `src/devtemplate/container.py` — `translate_published_ports`; `ports=` on
  `run_container`; docstring note on drift.
- `tests/test_forward.py` — new.
- `tests/test_container.py` — additions.
- `tests/integration/test_port_forward.py` — new.
- `README.md`, `CHANGELOG.md`.
