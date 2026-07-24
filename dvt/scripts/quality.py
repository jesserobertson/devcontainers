#!/usr/bin/env python3
"""
Code quality management script with mypy and ruff.
Unified interface for all code quality tasks.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table

from utils import run_command

app = typer.Typer(name="quality", help="Code Quality Management Script", add_completion=False)
console = Console()


@app.command()
def check() -> None:
    """Run all quality checks (typecheck + lint + format check)."""
    console.print(Panel.fit("Running All Code Quality Checks", style="blue"))
    results: dict[str, str] = {}

    with Status("Running mypy...", console=console, spinner="dots"):
        result = run_command(["mypy", "src"], check=False)
        results["Type Check"] = "Pass" if result.success else "Fail"

    with Status("Running ruff lint...", console=console, spinner="dots"):
        result = run_command(["ruff", "check", "src", "tests"], check=False)
        results["Linting"] = "Pass" if result.success else "Fail"

    with Status("Checking formatting...", console=console, spinner="dots"):
        result = run_command(["ruff", "format", "--check", "src", "tests"], check=False)
        results["Formatting"] = "Pass" if result.success else "Fail"

    table = Table(title="Quality Check Results", show_header=True, header_style="bold magenta")
    table.add_column("Check", style="cyan")
    table.add_column("Result", justify="center")
    for name, status in results.items():
        style = "green" if status == "Pass" else "red"
        table.add_row(name, f"[{style}]{status}[/{style}]")
    console.print(table)

    if "Fail" in results.values():
        console.print("\n[red]Some quality checks failed.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]All quality checks passed![/green]")


@app.command()
def typecheck() -> None:
    """Run mypy type checking."""
    console.print("Running mypy...")
    run_command(["mypy", "src"])
    console.print("[green]Type checking passed.[/green]")


@app.command()
def lint() -> None:
    """Run ruff linting (check only)."""
    console.print("Running ruff lint...")
    run_command(["ruff", "check", "src", "tests"])
    console.print("[green]Linting passed.[/green]")


@app.command(name="format")
def format_(
    check_only: bool = typer.Option(False, "--check", help="Check formatting without changing files"),
) -> None:
    """Format code with ruff (or check formatting with --check)."""
    if check_only:
        console.print("Checking formatting...")
        run_command(["ruff", "format", "--check", "src", "tests"])
        console.print("[green]Formatting is correct.[/green]")
    else:
        console.print("Formatting code...")
        run_command(["ruff", "format", "src", "tests"])
        console.print("[green]Code formatted.[/green]")


@app.command()
def fix() -> None:
    """Auto-fix everything possible (format + lint --fix)."""
    console.print("Auto-fixing code issues...")
    with Status("Formatting...", console=console, spinner="dots"):
        run_command(["ruff", "format", "src", "tests"])
    with Status("Fixing lint issues...", console=console, spinner="dots"):
        run_command(["ruff", "check", "--fix", "src", "tests"])
    console.print("[green]Auto-fix completed.[/green]")
    console.print("[yellow]Run 'pixi run quality check' to verify everything is resolved.[/yellow]")


@app.command()
def coverage(
    html: bool = typer.Option(False, "--html", help="Generate an HTML coverage report"),
) -> None:
    """Show or generate a coverage report (coverage.py is already installed via pytest-cov)."""
    if html:
        console.print("Generating HTML coverage report...")
        run_command(["coverage", "html"])
        console.print("[green]Report generated: htmlcov/index.html[/green]")
    else:
        run_command(["coverage", "report", "--show-missing"])


if __name__ == "__main__":
    app()
