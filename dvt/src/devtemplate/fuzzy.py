from __future__ import annotations

import difflib
import functools
import inspect
from collections.abc import Callable
from typing import Any

import typer
from logerr import Err, Ok, Result
from rich.console import Console

from devtemplate.cli_support import unwrap_or_exit
from devtemplate.config import Settings, load_settings

__all__ = ["fuzzy_argument", "resolve_or_confirm"]


def resolve_or_confirm(
    query: str,
    candidates: list[str],
    *,
    label: str,
    assume_yes: bool = False,
    interactive: bool = True,
) -> Result[str, Exception]:
    """Resolve `query` against `candidates`.

    An exact match passes through unchanged with no prompt. Otherwise the
    closest candidate (stdlib difflib, cutoff 0.6) is either auto-accepted
    (assume_yes), confirmed interactively via typer.confirm, or - when
    interactive=False, e.g. under --json - reported as a suggestion inside
    a plain Err rather than prompted for, so a script never hangs on an
    unanswerable question. No close match at all is a plain Err listing
    every known candidate.
    """
    if query in candidates:
        return Ok(query)

    matches = difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)
    match matches:
        case []:
            known = ", ".join(sorted(candidates)) or "(none cached)"
            return Err(
                ValueError(f"No {label} named {query!r}. Known {label}s: {known}")
            )
        case [match, *_]:
            if assume_yes:
                return Ok(match)
            if not interactive:
                return Err(
                    ValueError(
                        f"No {label} named {query!r}. Did you mean {match!r}? "
                        "Re-run with --yes to accept it, or pass the exact name."
                    )
                )
            if typer.confirm(f"No {label} named '{query}'. Did you mean '{match}'?"):
                return Ok(match)
            return Err(ValueError(f"Aborted: no {label} named {query!r}."))
        case _:
            raise AssertionError("unreachable")


def fuzzy_argument(
    param: str,
    *,
    candidates_fn: Callable[[Settings], list[str]],
    label: str,
    console: Console,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator for a typer command: fuzzy-resolve `param`'s value (a str,
    or a list[str] for a multi-value argument) against candidates_fn(settings)
    before the wrapped function runs, and inject a standardized --yes/-y
    option (kwarg `assume_yes`) into its signature so every command using
    this decorator gets the same flag name, message format, and exit
    behavior. On no match (or a declined confirmation) this exits via
    unwrap_or_exit before the wrapped function is ever called - it only
    ever sees an already-resolved name (or list of names).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        original_sig = inspect.signature(func)
        yes_param = inspect.Parameter(
            "assume_yes",
            inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                False,
                "--yes",
                "-y",
                help=f"Auto-accept a fuzzy-matched {label} name instead of prompting.",
            ),
            annotation=bool,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            assume_yes = kwargs.pop("assume_yes", False)
            json_output = kwargs.get("json_output", False)
            settings = unwrap_or_exit(load_settings(), console, json_output=json_output)
            candidates = candidates_fn(settings)

            def resolve_one(value: str) -> str:
                result = resolve_or_confirm(
                    value,
                    candidates,
                    label=label,
                    assume_yes=assume_yes,
                    interactive=not json_output,
                )
                return unwrap_or_exit(result, console, json_output=json_output)

            raw = kwargs[param]
            kwargs[param] = (
                [resolve_one(value) for value in raw]
                if isinstance(raw, list)
                else resolve_one(raw)
            )
            return func(*args, **kwargs)

        wrapper.__signature__ = original_sig.replace(  # type: ignore[attr-defined]
            parameters=[*original_sig.parameters.values(), yes_param]
        )
        return wrapper

    return decorator
