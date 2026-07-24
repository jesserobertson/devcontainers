#!/usr/bin/env python3
"""
Testing management script with pytest.
Unified interface for unit and integration test tiers.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.status import Status

from utils import run_command

app = typer.Typer(name="test", help="Testing Management Script", add_completion=False)
console = Console()

PROJECT_ROOT = Path(__file__).parent.parent


@app.command()
def unit(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    fail_fast: bool = typer.Option(False, "--fail-fast", "-x", help="Stop on first failure"),
) -> None:
    """Run unit tests (everything not marked integration/slow/network)."""
    console.print(Panel.fit("Running Unit Tests", style="blue"))
    cmd = ["pytest", "tests/", "-m", "unit"]
    if verbose:
        cmd.append("-v")
    if fail_fast:
        cmd.append("-x")
    with Status("Running unit tests...", console=console, spinner="dots"):
        run_command(cmd)
    console.print("[green]Unit tests completed.[/green]")


@app.command()
def integration(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run integration tests (real devpod/network calls).

    Opt-in only: creates real containers via a real devpod, skipped automatically if
    devpod isn't installed. Not run by `unit`, `fast`, `all`, or plain `pytest` — this
    is the only command that runs them.
    """
    console.print(Panel.fit("Running Integration Tests", style="blue"))
    cmd = ["pytest", "tests/", "-m", "integration"]
    if verbose:
        cmd.append("-v")
    with Status("Running integration tests...", console=console, spinner="dots"):
        run_command(cmd)
    console.print("[green]Integration tests completed.[/green]")


@app.command()
def all(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run the entire hermetic test suite (everything except integration).

    Integration tests are opt-in only, via `pixi run test integration` — never run
    here, since they create real containers via a real devpod.
    """
    console.print(Panel.fit("Running All Tests", style="blue"))
    cmd = ["pytest", "tests/", "-m", "not integration"]
    if verbose:
        cmd.append("-v")
    with Status("Running all tests...", console=console, spinner="dots"):
        run_command(cmd)
    console.print("[green]All tests completed.[/green]")


@app.command()
def fast(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run tests excluding anything marked slow or integration."""
    console.print(Panel.fit("Running Fast Tests", style="blue"))
    cmd = ["pytest", "tests/", "-m", "not slow and not integration"]
    if verbose:
        cmd.append("-v")
    with Status("Running fast tests...", console=console, spinner="dots"):
        run_command(cmd)
    console.print("[green]Fast tests completed.[/green]")


@app.command()
def clean() -> None:
    """Clean test artifacts (coverage reports, pytest cache, __pycache__)."""
    console.print("Cleaning test artifacts...")
    cleaned: list[str] = []

    for path in (PROJECT_ROOT / "htmlcov", PROJECT_ROOT / ".coverage", PROJECT_ROOT / ".pytest_cache"):
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            cleaned.append(path.name)

    for cache_dir in PROJECT_ROOT.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir)
            cleaned.append(str(cache_dir.relative_to(PROJECT_ROOT)))

    if cleaned:
        console.print("[green]Cleaned:[/green] " + ", ".join(cleaned))
    else:
        console.print("[yellow]Nothing to clean.[/yellow]")


if __name__ == "__main__":
    app()
