from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer
from logerr import Result
from rich.console import Console
from rich.markup import escape
from rich.status import Status

__all__ = [
    "emit_success",
    "report_error",
    "unwrap_or_exit",
    "with_status",
]


def report_error(message: str, console: Console, *, json_output: bool = False) -> None:
    """Report a plain-string failure in whichever shape --json calls for -
    {"ok": false, "error": message} on stdout, or Rich red text otherwise.
    Does not exit; callers that need to (most do) raise typer.Exit
    themselves right after, same as unwrap_or_exit's own contract. Exists
    separately from unwrap_or_exit for the errors that arise outside a
    Result (e.g. a lookup that returns None rather than an Err)."""
    if json_output:
        print(json.dumps({"ok": False, "error": message}))
    else:
        console.print(f"[red]{escape(message)}[/red]")


def unwrap_or_exit[T](
    result: Result[T, Exception],
    console: Console,
    prefix: str = "",
    *,
    json_output: bool = False,
) -> T:
    """Unwrap a Result or report its error (optionally prefixed) and exit(1).

    Deliberately an if/else on is_ok(), not Result.unwrap_or_else(): Err's
    unwrap_or_else() catches any exception its callback raises and re-wraps
    it in a RuntimeError, which would mangle the typer.Exit this needs to
    raise cleanly for Typer to report the exit code without an ugly
    traceback. An if/else also gives mypy a provable "every path
    returns/raises" without relying on match-exhaustiveness over an ABC.

    json_output=True prints {"ok": false, "error": "..."} on stdout instead
    of Rich red text, so a caller parsing dvt's output gets one consistent
    shape regardless of which command failed or how - see emit_success for
    the matching success-path convention.
    """
    if result.is_ok():
        return result.unwrap()
    report_error(f"{prefix}{result.unwrap_err()}", console, json_output=json_output)
    raise typer.Exit(code=1)


def emit_success(
    json_output: bool, payload: dict[str, Any], human: Callable[[], None]
) -> None:
    """Report a command's success either as {"ok": true, **payload} on
    stdout (json_output=True) or via the given human-readable callback
    (Rich console output) otherwise - the success-path counterpart to
    unwrap_or_exit's error-path convention."""
    if json_output:
        print(json.dumps({"ok": True, **payload}))
    else:
        human()


def with_status[T](
    json_output: bool, console: Console, message: str, fn: Callable[[Status | None], T]
) -> T:
    """Run fn(status), showing a Rich status spinner with `message` while it
    runs - unless json_output is set, in which case fn runs silently
    (passed None instead of a live status) since a spinner's live ANSI
    redraws would corrupt --json's single-line machine-readable output.

    Most callers (stop, delete, sync, feature add) ignore the status
    argument entirely - it exists for up's on_stage wiring, which needs
    the live Status object itself (status.update) to report sub-stage
    progress from inside up_workspace, not just a single start/end spinner.
    """
    if json_output:
        return fn(None)
    with console.status(message, spinner="dots") as status:
        return fn(status)
