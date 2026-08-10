from __future__ import annotations

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
from devtemplate.cli_support import unwrap_or_exit
from devtemplate.commands import feature
from devtemplate.commands.info import info as info_command
from devtemplate.commands.init import init as init_command
from devtemplate.config import load_settings
from devtemplate.container import find_workspace_container
from devtemplate.runtime import get_client
from devtemplate.ssh import exec_interactive, remove_ssh_config_entry, stdio_proxy
from devtemplate.workspace import up_workspace
from devtemplate.workspace_lookup import resolve_existing, resolve_for_up

app = typer.Typer(
    help="dvt: dev-style named devcontainer templates, built and run via Docker/Podman."
)
app.add_typer(feature.app, name="feature")
app.command("init")(init_command)
app.command("info")(info_command)
console = Console()


def _version_callback(value: bool) -> None:
    if not value:
        return
    console.print(f"dvt {__version__}")
    raise typer.Exit()


@app.callback()
def _root_callback(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show dvt's version and exit.",
    ),
) -> None:
    return


@app.command()
def up(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name for the new workspace (default: inferred from the current folder).",
    ),
) -> None:
    """Build and run a workspace from ./.devcontainer/devcontainer.json."""
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
        resolve_for_up(handle.client, name, Path.cwd()), console
    )
    unwrap_or_exit(up_workspace(handle, settings, resolved_name, Path.cwd()), console)
    console.print(
        f"[green]Workspace '{resolved_name}' is up.[/green] "
        f"Connect with: dvt ssh {resolved_name} "
        f"(plain 'ssh {resolved_name}' also works, via the ~/.ssh/config entry dvt just wrote)"
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
    # is speaking the protocol over (see ssh_server.py), so every diagnostic has
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


def _find_or_exit(client: DockerClient, name: str) -> Container:
    try:
        container = find_workspace_container(client, name)
    except Exception as exc:
        console.print(
            f"[red]Failed to look up workspace '{escape(name)}': {escape(str(exc))}[/red]"
        )
        raise typer.Exit(code=1) from exc
    if container is None:
        console.print(f"[red]No workspace named '{escape(name)}' found.[/red]")
        raise typer.Exit(code=1)
    return container


@app.command()
def stop(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to stop (default: inferred from the current folder).",
    ),
) -> None:
    """Stop a running workspace."""
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
        resolve_existing(handle.client, name, Path.cwd(), "stop"), console
    )
    container = _find_or_exit(handle.client, resolved_name)
    try:
        container.stop()
    except Exception as exc:
        console.print(
            f"[red]Failed to stop '{escape(resolved_name)}': {escape(str(exc))}[/red]"
        )
        raise typer.Exit(code=1) from exc
    console.print(f"Stopped '{resolved_name}'.")


@app.command()
def delete(
    name: str | None = typer.Argument(  # noqa: B008
        None,
        help="Name of the workspace to delete (default: inferred from the current folder).",
    ),
) -> None:
    """Delete a workspace's container (the built image is left cached)."""
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
        resolve_existing(handle.client, name, Path.cwd(), "delete"), console
    )
    container = _find_or_exit(handle.client, resolved_name)
    try:
        container.remove(force=True)
    except Exception as exc:
        console.print(
            f"[red]Failed to delete '{escape(resolved_name)}': {escape(str(exc))}[/red]"
        )
        raise typer.Exit(code=1) from exc
    unwrap_or_exit(
        remove_ssh_config_entry(resolved_name, Path.home() / ".ssh" / "config"), console
    )
    console.print(f"Deleted '{resolved_name}'.")


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
