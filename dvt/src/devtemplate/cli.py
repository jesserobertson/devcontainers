from __future__ import annotations

import sys
from pathlib import Path

import httpx
import logerr
import typer
from docker.client import DockerClient
from docker.models.containers import Container

# Err/Ok are re-exported here for tests to monkeypatch fakes via
# `cli_module.Ok(...)`/`cli_module.Err(...)` without importing logerr directly.
from logerr import Err, Ok, Result  # noqa: F401
from logerr.utilities import wrap_result
from loguru import logger
from rich.console import Console
from rich.markup import escape

from devtemplate import __version__, describe
from devtemplate.cli_output_schemas import attach_output_schema
from devtemplate.cli_support import emit_success, unwrap_or_exit, with_status
from devtemplate.commands import feature_app, image_app, info_command, init_command
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_container
from devtemplate.features import clear_pulled_features
from devtemplate.forward import block_forever, build_forwarder
from devtemplate.images import sync_images
from devtemplate.runtime import get_client
from devtemplate.ssh import (
    exec_command,
    exec_interactive,
    remove_ssh_config_entry,
    stdio_proxy,
)
from devtemplate.store import sync_templates
from devtemplate.workspace import resolve_existing, resolve_for_up, up_workspace

# Wires --describe (on every command and sub-group, via describe.Typer) to
# report dvt's version and to attach each command's output JSON Schema.
describe.configure(
    version=__version__,
    version_key="dvt_version",
    enrich=attach_output_schema,
)

app = describe.Typer(
    help="dvt: dev-style named devcontainer templates, built and run via Docker/Podman."
)
app.add_typer(feature_app, name="feature")
app.add_typer(image_app, name="image")
app.command("init")(init_command)
app.command("info")(info_command)
console = Console()

__all__ = ["app", "main"]


def version_callback(value: bool) -> None:
    if not value:
        return
    console.print(f"dvt {__version__}")
    raise typer.Exit()


@app.callback()
def root_callback(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="Show dvt's version and exit.",
    ),
    verbose: bool = typer.Option(  # noqa: B008
        False,
        "--verbose",
        "-v",
        help="Log INFO-level diagnostics (including logerr's Result-error "
        "logging) to stderr.",
    ),
    debug: bool = typer.Option(  # noqa: B008
        False,
        "--debug",
        help="Log DEBUG-level diagnostics (including logerr's Result-error "
        "logging) to stderr. Takes precedence over --verbose.",
    ),
) -> None:
    if debug or verbose:
        level = "DEBUG" if debug else "INFO"
        logger.add(sys.stderr, level=level)
        logerr.configure(enabled=True, level=level)


@app.command()
def sync(
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Refresh the cached feature and image registries from GitHub.

    Also clears the local cache of pulled devcontainer spec Feature artifacts
    (the OCI ref each template's "features" map points at, e.g.
    "ghcr.io/.../py-devtools:latest") - `dvt up` caches those forever once
    pulled once (see devtemplate.features.pull_feature), which is correct for
    an immutable version tag but means a moved `:latest` upstream would
    otherwise never be noticed on a machine that already pulled it. `sync` is
    the existing "go get whatever's current" entry point, so it clears both.
    """
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)

    clear_pulled_features(settings.features_dir)

    @wrap_result
    def do_sync(_status: object) -> dict[str, list[str]]:
        with httpx.Client() as client:
            features = sync_templates(settings, client).unwrap()
            images = sync_images(settings, client).unwrap()
        return {"features": features, "images": images}

    result = with_status(
        json_output, console, "Syncing features and images from GitHub...", do_sync
    )
    synced = unwrap_or_exit(
        result, console, prefix="Sync failed: ", json_output=json_output
    )
    emit_success(
        json_output,
        synced,
        lambda: console.print(
            f"Synced {len(synced['features'])} features: "
            f"{', '.join(synced['features'])}\n"
            f"Synced {len(synced['images'])} images: "
            f"{', '.join(synced['images'])}"
        ),
    )


@app.command()
def up(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name for the new workspace (default: inferred from the current folder).",
    ),
    rebuild: bool = typer.Option(  # noqa: B008
        False,
        "--rebuild",
        help="Force a fresh rebuild, discarding the existing container and cached image.",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Build and run a workspace from ./.devcontainer/devcontainer.json."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
        json_output=json_output,
    )
    resolved_name = unwrap_or_exit(
        resolve_for_up(handle.client, name, Path.cwd()),
        console,
        json_output=json_output,
    )
    result = with_status(
        json_output,
        console,
        "Starting workspace...",
        # up_workspace's own on_stage default is a no-op lambda, not None -
        # passing on_stage=None outright (json_output mode has no status)
        # would crash the first time up_workspace calls it.
        lambda status: up_workspace(
            handle,
            settings,
            resolved_name,
            Path.cwd(),
            rebuild=rebuild,
            on_stage=status.update if status else (lambda _stage: None),
        ),
    )
    unwrap_or_exit(result, console, json_output=json_output)
    emit_success(
        json_output,
        {"name": resolved_name},
        lambda: console.print(
            f"[green]Workspace '{escape(resolved_name)}' is up.[/green] "
            f"Connect with: dvt ssh {escape(resolved_name)} "
            f"(plain 'ssh {escape(resolved_name)}' also works, via the ~/.ssh/config entry dvt just wrote)"
        ),
    )


@app.command()
def ssh(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to connect to (default: inferred from the current folder).",
    ),
    stdio: bool = typer.Option(  # noqa: B008
        False,
        "--stdio",
        help="Non-interactive pipe mode for ProxyCommand use.",
        hidden=True,
    ),
    forward: list[str] = typer.Option(  # noqa: B008
        [],
        "--forward",
        "-L",
        help="Forward a host port to a server inside the workspace for the "
        "lifetime of this session, e.g. -L 2718 (repeatable). Spec: "
        "LOCAL[:REMOTE_HOST:]REMOTE. Ignored in --stdio mode.",
    ),
) -> None:
    """SSH into a running workspace (or, with --stdio, pipe stdio for ProxyCommand)."""
    # In --stdio mode this process's stdout *is* the SSH byte stream the client
    # is speaking the protocol over (see devtemplate.sshd), so every diagnostic has
    # to go to stderr instead - printing one on stdout injects garbage into the
    # handshake and the user sees `kex_exchange_identification: Connection
    # closed by remote host` rather than dvt's actual message. Reachable on a
    # perfectly ordinary state: the ~/.ssh/config entry survives `dvt stop`
    # (only `dvt delete` removes it), so `ssh <name>` on a stopped workspace
    # takes exactly this path. The interactive branch below keeps the shared
    # stdout console - there, stdout is the user's terminal, not a byte stream.
    errors = Console(stderr=True) if stdio else console
    settings = unwrap_or_exit(load_settings(), errors)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        errors,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "ssh"), errors
    )
    if stdio:
        exit_code = unwrap_or_exit(
            stdio_proxy(handle.cli_binary, handle.client, resolved_name), errors
        )
        raise typer.Exit(code=exit_code)

    forwarder = (
        unwrap_or_exit(
            build_forwarder(handle.client, handle.cli_binary, resolved_name, forward),
            errors,
        )
        if forward
        else None
    )
    try:
        exit_code = unwrap_or_exit(
            exec_interactive(handle.cli_binary, handle.client, resolved_name), errors
        )
    finally:
        if forwarder is not None:
            forwarder.close()
    raise typer.Exit(code=exit_code)


@app.command(
    context_settings={
        "ignore_unknown_options": True,
        # Stop option parsing at the first positional so a flag that belongs to
        # the user's command (curl/grep/ls/tar/make all take -L; also -t) isn't
        # captured as one of dvt's own -n/-t/-L. dvt's options must precede the
        # command, exactly as this command's docstring already promises.
        "allow_interspersed_args": False,
    }
)
def run(
    command: list[str] = typer.Argument(  # noqa: B008
        ...,
        help="Command (and its arguments) to run inside the workspace, e.g. "
        "'dvt run -n web pytest -q'. Options meant for dvt itself "
        "(-n/--name, -t/--tty, -L/--forward) must come before the command; "
        "put '--' before the command to separate them explicitly.",
    ),
    name: str | None = typer.Option(  # noqa: B008
        None,
        "--name",
        "-n",
        help="Name of the workspace to run in (default: inferred from the "
        "current folder).",
    ),
    tty: bool = typer.Option(  # noqa: B008
        False,
        "--tty",
        "-t",
        help="Allocate a TTY (needed for interactive programs like a REPL); "
        "leave off when capturing output or piping.",
    ),
    forward: list[str] = typer.Option(  # noqa: B008
        [],
        "--forward",
        "-L",
        help="Forward a host port to a server inside the workspace for the "
        "lifetime of this command, e.g. -L 2718 (repeatable). Spec: "
        "LOCAL[:REMOTE_HOST:]REMOTE.",
    ),
) -> None:
    """Run a command inside a running workspace and exit with its status.

    Options meant for dvt itself (-n/--name, -t/--tty, -L/--forward) must come
    before the command; everything from the first non-option argument onward is
    the command and its own arguments (so `dvt run -n web curl -L <url>` passes
    -L to curl). Use `--` before the command to separate them explicitly.

    The command runs through the workspace user's login shell so image
    shell-startup hooks (e.g. a project's pixi environment) apply, the same
    way `dvt ssh` gets them.
    """
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "run"), console
    )
    forwarder = (
        unwrap_or_exit(
            build_forwarder(handle.client, handle.cli_binary, resolved_name, forward),
            console,
        )
        if forward
        else None
    )
    try:
        exit_code = unwrap_or_exit(
            exec_command(
                handle.cli_binary, handle.client, resolved_name, command, tty=tty
            ),
            console,
        )
    finally:
        if forwarder is not None:
            forwarder.close()
    raise typer.Exit(code=exit_code)


@app.command()
def forward(
    specs: list[str] = typer.Argument(  # noqa: B008
        ...,
        metavar="SPEC...",
        help="Port forward(s), each LOCAL[:REMOTE_HOST:]REMOTE "
        "(default REMOTE_HOST=127.0.0.1, LOCAL=REMOTE). Repeatable: "
        "'dvt forward -n web 2718 8080:3000'.",
    ),
    name: str | None = typer.Option(  # noqa: B008
        None,
        "--name",
        "-n",
        help="Workspace to forward into (default: inferred from the current folder).",
    ),
) -> None:
    """Forward host ports to a server running inside a workspace, over the
    existing `dvt ssh` transport - no container rebuild, no host networking.

    Runs in the foreground until interrupted (Ctrl-C). Handy for a dev server
    started with `dvt run`, e.g. `marimo edit --port 2718` reachable at
    http://localhost:2718 on the host.
    """
    settings = unwrap_or_exit(load_settings(), console)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "forward"), console
    )
    forwarder = unwrap_or_exit(
        build_forwarder(handle.client, handle.cli_binary, resolved_name, specs), console
    )
    for line in forwarder.summary_lines():
        console.print(line)
    console.print("[dim]Forwarding until interrupted (Ctrl-C to stop).[/dim]")
    try:
        block_forever()
    finally:
        forwarder.close()
    console.print("Stopped forwarding.")


@wrap_result
def find_workspace_container_result(client: DockerClient, name: str) -> Container:
    """Result-returning wrapper (via @wrap_result) around
    find_workspace_container: folds both
    of its failure modes (the lookup call itself raising, and it returning
    None for "no such workspace") into a single Err, so callers get one
    uniform error path via unwrap_or_exit instead of a try/except plus a
    separate None-check."""
    try:
        container = find_workspace_container(client, name)
    except Exception as exc:
        raise RuntimeError(f"Failed to look up workspace '{name}': {exc}") from exc
    if container is None:
        raise LookupError(f"No workspace named '{name}' found.")
    return container


def find_or_exit(
    client: DockerClient, name: str, *, json_output: bool = False
) -> Container:
    return unwrap_or_exit(
        find_workspace_container_result(client, name), console, json_output=json_output
    )


@wrap_result
def stop_container(container: Container) -> Result[None, Exception]:
    container.stop()
    return Ok(None)


@wrap_result
def delete_container(container: Container) -> Result[None, Exception]:
    container.remove(force=True)
    return Ok(None)


@app.command()
def stop(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to stop (default: inferred from the current folder).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Stop a running workspace."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
        json_output=json_output,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "stop"),
        console,
        json_output=json_output,
    )
    container = find_or_exit(handle.client, resolved_name, json_output=json_output)
    result = with_status(
        json_output,
        console,
        f"Stopping '{resolved_name}'...",
        lambda _status: stop_container(container),
    )
    unwrap_or_exit(
        result,
        console,
        prefix=f"Failed to stop '{resolved_name}': ",
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": resolved_name},
        lambda: console.print(f"Stopped '{escape(resolved_name)}'."),
    )


@app.command()
def delete(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to delete (default: inferred from the current folder).",
    ),
    json_output: bool = typer.Option(  # noqa: B008
        False,
        "--json",
        help="Print machine-readable JSON instead of human-readable text.",
    ),
) -> None:
    """Delete a workspace's container (the built image is left cached)."""
    settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
    handle = unwrap_or_exit(
        get_client(
            settings.runtime,
            podman_machine_auto_init=settings.podman_machine_auto_init,
            podman_machine_auto_start=settings.podman_machine_auto_start,
        ),
        console,
        json_output=json_output,
    )
    resolved_name = unwrap_or_exit(
        resolve_existing(handle.client, name, Path.cwd(), "delete"),
        console,
        json_output=json_output,
    )
    container = find_or_exit(handle.client, resolved_name, json_output=json_output)
    result = with_status(
        json_output,
        console,
        f"Deleting '{resolved_name}'...",
        lambda _status: delete_container(container),
    )
    unwrap_or_exit(
        result,
        console,
        prefix=f"Failed to delete '{resolved_name}': ",
        json_output=json_output,
    )
    unwrap_or_exit(
        remove_ssh_config_entry(resolved_name, Path.home() / ".ssh" / "config"),
        console,
        json_output=json_output,
    )
    emit_success(
        json_output,
        {"name": resolved_name},
        lambda: console.print(f"Deleted '{escape(resolved_name)}'."),
    )


def main() -> None:
    # Removes loguru's default stderr sink entirely — logerr.configure(enabled=False)
    # only stops logerr's own Err-construction auto-logging; it doesn't touch other
    # code (e.g. logerr.recipes.retry's own direct logger.debug(...) calls) that
    # writes to loguru's shared logger directly. With no sink at all, nothing from
    # either path reaches the console, keeping dvt's own Rich-formatted messages as
    # the only user-facing output.
    logger.remove()
    logerr.configure(enabled=False)
    app()


if __name__ == "__main__":
    main()
