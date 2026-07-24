from __future__ import annotations

import subprocess

import typer
from logerr import Err, Ok, Result
from rich.console import Console

from devtemplate.commands import project, template

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")
app.add_typer(template.app, name="template")
app.add_typer(project.app, name="project")
console = Console()


def _run_devpod(subcommand: str, name: str, extra_args: list[str]) -> Result[int, Exception]:
    """Run a devpod subcommand, forwarding its exit code.

    Deliberately not retried: unlike the GitHub API calls in github.py, a devpod
    subcommand's exit code is meaningful output to forward to the user (e.g.
    `dvt ssh proj -- pytest` should return pytest's real exit code, not something
    dvt silently retries past), not a transient failure. Only a genuine launch
    failure (devpod missing from PATH, etc.) is an Err here.
    """
    try:
        result = subprocess.run(["devpod", subcommand, name, *extra_args])
        return Ok(result.returncode)
    except Exception as exc:
        return Err(exc)


def _devpod_passthrough(subcommand: str, name: str, extra_args: list[str]) -> None:
    match _run_devpod(subcommand, name, extra_args):
        case Ok(returncode):
            raise typer.Exit(code=returncode)
        case Err(error):
            console.print(f"[red]Failed to run devpod {subcommand}: {error}[/red]")
            raise typer.Exit(code=1)


@app.command()
def up(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod up."),
) -> None:
    """Passthrough to `devpod up`."""
    _devpod_passthrough("up", name, extra_args or [])


@app.command()
def ssh(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod ssh."),
) -> None:
    """Passthrough to `devpod ssh`."""
    _devpod_passthrough("ssh", name, extra_args or [])


@app.command()
def stop(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod stop."),
) -> None:
    """Passthrough to `devpod stop`."""
    _devpod_passthrough("stop", name, extra_args or [])


@app.command()
def delete(
    name: str,
    extra_args: list[str] = typer.Argument(None, help="Extra args forwarded to devpod delete."),
) -> None:
    """Passthrough to `devpod delete`."""
    _devpod_passthrough("delete", name, extra_args or [])


def main() -> None:
    app()


if __name__ == "__main__":
    main()
