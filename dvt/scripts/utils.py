#!/usr/bin/env python3
"""
Shared utilities for script commands.

Uses subprocess, not the `sh` library the pattern this is based on normally uses — `sh`
explicitly doesn't support Windows, and dvt is developed and CI-tested on Windows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console

console = Console()

PROJECT_ROOT = Path(__file__).parent.parent


class CommandResult:
    """Result wrapper for command execution."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def success(self) -> bool:
        return self.returncode == 0


def run_command(
    cmd: list[str], capture_output: bool = False, check: bool = True
) -> CommandResult:
    """Run a command with proper error handling.

    Args:
        cmd: Command and arguments as a list.
        capture_output: Whether to capture stdout/stderr instead of streaming them.
        check: Whether to raise typer.Exit on a non-zero exit code.

    Returns:
        CommandResult with returncode, stdout, stderr.

    Raises:
        typer.Exit: If check=True and the command fails or isn't found.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=capture_output,
            text=True,
        )
    except FileNotFoundError as exc:
        console.print(f"[red]Command not found: {cmd[0]}[/red]")
        if check:
            raise typer.Exit(1) from exc
        return CommandResult(returncode=1, stderr=f"Command not found: {cmd[0]}")

    if result.returncode != 0:
        console.print(f"[red]Command failed: {' '.join(cmd)}[/red]")
        if capture_output:
            if result.stdout:
                console.print(f"[yellow]STDOUT: {result.stdout}[/yellow]")
            if result.stderr:
                console.print(f"[red]STDERR: {result.stderr}[/red]")
        if check:
            raise typer.Exit(result.returncode)

    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )
