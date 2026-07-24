#!/usr/bin/env python3
"""
Documentation management script.
Unified interface for building, serving, and inspecting documentation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table

from utils import run_command

app = typer.Typer(name="docs", help="Documentation Management Script", add_completion=False)
console = Console()

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
SITE_DIR = DOCS_DIR / "site"
CONFIG_FILE = DOCS_DIR / "mkdocs.yml"


@app.command()
def serve(port: int = typer.Option(8000, "--port", "-p", help="Port to serve on")) -> None:
    """Serve documentation locally for development."""
    console.print(Panel.fit("Serving Documentation", style="blue"))
    console.print(f"[green]Documentation will be available at http://localhost:{port}[/green]")
    console.print("[yellow]Press Ctrl+C to stop.[/yellow]")
    try:
        run_command(
            ["mkdocs", "serve", "--config-file", str(CONFIG_FILE), "--dev-addr", f"localhost:{port}"]
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Documentation server stopped.[/yellow]")


@app.command()
def build(strict: bool = typer.Option(False, "--strict", help="Treat warnings as errors")) -> None:
    """Build documentation."""
    console.print(Panel.fit("Building Documentation", style="blue"))
    cmd = ["mkdocs", "build", "--config-file", str(CONFIG_FILE)]
    if strict:
        cmd.append("--strict")
    with Status("Building...", console=console, spinner="dots"):
        run_command(cmd)
    console.print(f"[green]Built to {SITE_DIR}[/green]")


@app.command()
def status() -> None:
    """Show documentation status."""
    table = Table(title="Documentation Status", show_header=True, header_style="bold magenta")
    table.add_column("Item", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    if CONFIG_FILE.exists():
        table.add_row("Configuration", "[green]Found[/green]", CONFIG_FILE.name)
    else:
        table.add_row("Configuration", "[red]Missing[/red]", CONFIG_FILE.name)

    if DOCS_DIR.exists():
        md_files = list(DOCS_DIR.rglob("*.md"))
        table.add_row("Source Files", "[green]Present[/green]", f"{len(md_files)} markdown files")
    else:
        table.add_row("Source Files", "[red]Missing[/red]", "docs/ not found")

    if SITE_DIR.exists():
        html_files = list(SITE_DIR.rglob("*.html"))
        table.add_row("Built Docs", "[green]Available[/green]", f"{len(html_files)} HTML files")
    else:
        table.add_row("Built Docs", "[yellow]Not built[/yellow]", "Run 'pixi run docs build'")

    console.print(table)


@app.command()
def clean() -> None:
    """Clean documentation build artifacts."""
    console.print("Cleaning documentation artifacts...")
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
        console.print("[green]Cleaned:[/green] site/")
    else:
        console.print("[yellow]Nothing to clean.[/yellow]")


if __name__ == "__main__":
    app()
