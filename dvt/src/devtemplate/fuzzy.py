from __future__ import annotations

import difflib

import typer
from logerr import Err, Ok, Result

__all__ = ["resolve_or_confirm"]


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
            return Err(ValueError(f"No {label} named {query!r}. Known {label}s: {known}"))
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
