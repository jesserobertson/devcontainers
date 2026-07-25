from __future__ import annotations

import typer
from logerr import Result
from rich.console import Console
from rich.markup import escape


def unwrap_or_exit[T](
    result: Result[T, Exception], console: Console, prefix: str = ""
) -> T:
    """Unwrap a Result or print its error (optionally prefixed) and exit(1).

    Deliberately an if/else on is_ok(), not Result.unwrap_or_else(): Err's
    unwrap_or_else() catches any exception its callback raises and re-wraps
    it in a RuntimeError, which would mangle the typer.Exit this needs to
    raise cleanly for Typer to report the exit code without an ugly
    traceback. An if/else also gives mypy a provable "every path
    returns/raises" without relying on match-exhaustiveness over an ABC.
    """
    if result.is_ok():
        return result.unwrap()
    console.print(f"[red]{prefix}{escape(str(result.unwrap_err()))}[/red]")
    raise typer.Exit(code=1)
