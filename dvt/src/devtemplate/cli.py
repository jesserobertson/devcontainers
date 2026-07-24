from __future__ import annotations

import shutil
import subprocess

import logerr
import typer
from logerr import Err, Ok, Result
from loguru import logger
from rich.console import Console
from rich.markup import escape

from devtemplate.commands import project, template

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")
app.add_typer(template.app, name="template")
app.add_typer(project.app, name="project")
console = Console()


def _run_devpod(
    subcommand: str, name: str, extra_args: list[str]
) -> Result[int, Exception]:
    """Run a devpod subcommand, forwarding its exit code.

    Deliberately not retried: unlike the GitHub API calls in github.py, a devpod
    subcommand's exit code is meaningful output to forward to the user (e.g.
    `dvt ssh proj -- pytest` should return pytest's real exit code, not something
    dvt silently retries past), not a transient failure. Only a genuine launch
    failure (devpod missing from PATH, etc.) is an Err here.

    Resolves the executable via shutil.which() rather than passing the bare name
    "devpod" to subprocess.run: on Windows, devpod installs as devpod.CMD (plus an
    extensionless POSIX-shell variant) — Win32 CreateProcess (what subprocess.run
    uses without shell=True) cannot resolve a bare command name to a .CMD file the
    way a shell's own PATH/PATHEXT search does, so the un-resolved bare name fails
    with WinError 2 even though devpod is genuinely on PATH. shutil.which() applies
    the same PATHEXT-aware resolution a shell would, cross-platform.
    """
    devpod_executable = shutil.which("devpod")
    if devpod_executable is None:
        return Err(
            FileNotFoundError(
                "devpod not found on PATH. Install it from https://devpod.sh"
            )
        )
    try:
        result = subprocess.run([devpod_executable, subcommand, name, *extra_args])
        return Ok(result.returncode)
    except Exception as exc:
        return Err(exc)


def _devpod_passthrough(subcommand: str, name: str, extra_args: list[str]) -> None:
    match _run_devpod(subcommand, name, extra_args):
        case Ok(returncode):
            raise typer.Exit(code=returncode)
        case Err(error):
            console.print(
                f"[red]Failed to run devpod {escape(subcommand)}: {escape(str(error))}[/red]"
            )
            raise typer.Exit(code=1)


@app.command()
def up(
    name: str,
    extra_args: list[str] = typer.Argument(  # noqa: B008
        None, help="Extra args forwarded to devpod up."
    ),
) -> None:
    """Passthrough to `devpod up`."""
    _devpod_passthrough("up", name, extra_args or [])


@app.command()
def ssh(
    name: str,
    extra_args: list[str] = typer.Argument(  # noqa: B008
        None, help="Extra args forwarded to devpod ssh."
    ),
) -> None:
    """Passthrough to `devpod ssh`."""
    _devpod_passthrough("ssh", name, extra_args or [])


@app.command()
def stop(
    name: str,
    extra_args: list[str] = typer.Argument(  # noqa: B008
        None, help="Extra args forwarded to devpod stop."
    ),
) -> None:
    """Passthrough to `devpod stop`."""
    _devpod_passthrough("stop", name, extra_args or [])


@app.command()
def delete(
    name: str,
    extra_args: list[str] = typer.Argument(  # noqa: B008
        None, help="Extra args forwarded to devpod delete."
    ),
) -> None:
    """Passthrough to `devpod delete`."""
    _devpod_passthrough("delete", name, extra_args or [])


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
