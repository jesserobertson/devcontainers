from __future__ import annotations

import json
from pathlib import Path

import logerr
import typer
from docker.client import DockerClient
from docker.models.containers import Container

# Err/Ok are re-exported here for tests to monkeypatch fakes via
# `cli_module.Ok(...)`/`cli_module.Err(...)` without importing logerr directly.
from logerr import Err, Ok  # noqa: F401
from loguru import logger
from rich.console import Console
from rich.markup import escape

from devtemplate import __version__
from devtemplate.cli_support import (
    describe_app,
    emit_success,
    report_error,
    unwrap_or_exit,
)
from devtemplate.commands import feature_app, info_command, init_command
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client
from devtemplate.ssh import exec_interactive, remove_ssh_config_entry, stdio_proxy
from devtemplate.workspace import resolve_existing, resolve_for_up, up_workspace

app = typer.Typer(
    help="dvt: dev-style named devcontainer templates, built and run via Docker/Podman."
)
app.add_typer(feature_app, name="feature")
app.command("init")(init_command)
app.command("info")(info_command)
console = Console()

__all__ = ["app", "main"]


def version_callback(value: bool) -> None:
    if not value:
        return
    console.print(f"dvt {__version__}")
    raise typer.Exit()


def describe_callback(value: bool) -> None:
    if not value:
        return
    print(json.dumps(describe_app(app, version=__version__)))
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
    describe: bool = typer.Option(  # noqa: B008
        False,
        "--describe",
        callback=describe_callback,
        is_eager=True,
        help="Print a JSON manifest of every command and its args, then exit.",
    ),
) -> None:
    return


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
    unwrap_or_exit(
        up_workspace(handle, settings, resolved_name, Path.cwd(), rebuild=rebuild),
        console,
        json_output=json_output,
    )
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
    result = (
        stdio_proxy(handle.cli_binary, handle.client, resolved_name)
        if stdio
        else exec_interactive(handle.cli_binary, handle.client, resolved_name)
    )
    exit_code = unwrap_or_exit(result, errors)
    raise typer.Exit(code=exit_code)


def find_or_exit(
    client: DockerClient, name: str, *, json_output: bool = False
) -> Container:
    try:
        container = find_workspace_container(client, name)
    except Exception as exc:
        report_error(
            f"Failed to look up workspace '{name}': {exc}",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1) from exc
    if container is None:
        report_error(
            f"No workspace named '{name}' found.", console, json_output=json_output
        )
        raise typer.Exit(code=1)
    return container


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
    try:
        container.stop()
    except Exception as exc:
        report_error(
            f"Failed to stop '{resolved_name}': {exc}", console, json_output=json_output
        )
        raise typer.Exit(code=1) from exc
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
    try:
        container.remove(force=True)
    except Exception as exc:
        report_error(
            f"Failed to delete '{resolved_name}': {exc}",
            console,
            json_output=json_output,
        )
        raise typer.Exit(code=1) from exc
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
