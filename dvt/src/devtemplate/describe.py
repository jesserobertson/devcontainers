"""A scoped ``--describe`` flag for Typer CLIs, for agents and scripts that
want to discover a command's callable surface without parsing ``--help``.

``describe.Typer`` is a drop-in ``typer.Typer`` whose every command *and*
sub-group carries an eager ``--describe`` flag (like ``--help``). It prints a
JSON manifest - each command's description and args (name/kind/type/
required/flags), keyed by dotted name for nested groups (e.g. ``"feature
add"``) - scoped to wherever the flag appeared:

    dvt --describe             whole tree
    dvt feature --describe     just the "feature" subtree
    dvt feature add --describe just "feature add"

so an agent asking about one command doesn't pay for the whole tree.

Nothing here is project-specific; the one seam is ``configure()``, which
binds the CLI's version string and an optional ``enrich`` hook for adding
per-command detail the Click tree doesn't carry (dvt uses it to attach
output JSON Schemas). Call it once at startup, the way ``logging`` and
``logerr`` are configured.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import typer
import typer._click as _click
import typer.core
import typer.main

__all__ = ["Typer", "configure", "describe_app"]

EnrichFn = Callable[[str, dict[str, Any]], None]

_version = ""
_version_key = "version"
_enrich: EnrichFn | None = None


def configure(
    *, version: str, version_key: str = "version", enrich: EnrichFn | None = None
) -> None:
    """Bind what the injected ``--describe`` flag needs when it fires:

    - ``version`` - the CLI's version string, reported in the manifest.
    - ``version_key`` - the manifest key to file it under (e.g.
      ``"dvt_version"``).
    - ``enrich`` - optional ``(dotted_name, entry) -> None`` hook, called
      per leaf command to mutate its manifest entry in place. dvt uses it
      to attach output JSON Schemas.

    Call once at import time. Not thread-safe; it isn't meant to change
    after startup.
    """
    global _version, _version_key, _enrich
    _version, _version_key, _enrich = version, version_key, enrich


def describe_app(
    app: typer.Typer, *, version: str | None = None, only: str | None = None
) -> dict[str, Any]:
    """Introspect a Typer app's underlying Click command tree into the
    JSON-serializable manifest described in the module docstring.

    Args are read from the live command definitions, so they can't drift
    out of sync with what the CLI actually accepts; hidden params (and the
    injected ``--describe`` flag itself) are excluded, matching ``--help``.

    ``version`` overrides the value from ``configure()`` (tests pass it
    explicitly); ``only`` restricts the manifest to a single command's
    dotted name or a group's subtree.
    """
    return _describe_group(
        typer.main.get_command(app),
        version=_version if version is None else version,
        only=only,
    )


def _describe_group(
    root: _click.Command, *, version: str, only: str | None
) -> dict[str, Any]:
    commands: dict[str, Any] = {}
    if isinstance(root, typer.core.TyperGroup):
        _collect_commands(root, "", commands)
    if only is not None:
        commands = {
            name: spec
            for name, spec in commands.items()
            if name == only or name.startswith(f"{only} ")
        }
    return {_version_key: version, "commands": commands}


def _collect_commands(
    group: typer.core.TyperGroup, prefix: str, out: dict[str, Any]
) -> None:
    for name, command in group.commands.items():
        full_name = f"{prefix}{name}"
        if isinstance(command, typer.core.TyperGroup):
            _collect_commands(command, f"{full_name} ", out)
        else:
            out[full_name] = _describe_command(
                full_name, cast(typer.core.TyperCommand, command)
            )


def _describe_command(
    full_name: str, command: typer.core.TyperCommand
) -> dict[str, Any]:
    described: dict[str, Any] = {
        "description": (command.help or "").strip(),
        "args": [
            {
                "name": param.name,
                "kind": "argument"
                if isinstance(param, typer.core.TyperArgument)
                else "option",
                "type": param.type.name,
                "required": bool(param.required),
                "flags": list(param.opts),
            }
            for param in command.params
            if not getattr(param, "hidden", False)
            # --describe is the meta-flag describe.Typer injects onto every
            # command (like Click's own --help, which also never shows up
            # here); it isn't part of the command's real callable surface.
            and "--describe" not in param.opts
        ],
    }
    if _enrich is not None:
        _enrich(full_name, described)
    return described


def _describe_flag_callback(
    ctx: _click.Context, _param: _click.Parameter, value: bool
) -> None:
    """Eager callback for the injected ``--describe`` flag. Rebuilds the
    dotted command path from the Click context chain (dropping the root
    program name), prints the manifest scoped to it, and exits - so
    ``dvt feature add --describe`` emits just that command and
    ``dvt feature --describe`` just the feature subtree."""
    if not value or ctx.resilient_parsing:
        return
    parts: list[str] = []
    node: _click.Context | None = ctx
    while node is not None and node.parent is not None:
        if node.info_name:
            parts.append(node.info_name)
        node = node.parent
    parts.reverse()
    manifest = _describe_group(
        ctx.find_root().command,
        version=_version,
        only=" ".join(parts) or None,
    )
    print(json.dumps(manifest))
    ctx.exit()


def _describe_option() -> typer.core.TyperOption:
    return typer.core.TyperOption(
        param_decls=["--describe"],
        is_flag=True,
        is_eager=True,
        expose_value=False,
        callback=_describe_flag_callback,
        help="Print a JSON manifest of this command (and any subcommands), then exit.",
    )


def _add_describe_option(command: _click.Command) -> None:
    """Append the eager ``--describe`` option unless it's already present -
    the way Click appends its own ``--help``. Called from both the command
    and group subclasses so the flag rides every level of the tree."""
    if not any("--describe" in param.opts for param in command.params):
        command.params.append(_describe_option())


class DescribeCommand(typer.core.TyperCommand):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _add_describe_option(self)


class DescribeGroup(typer.core.TyperGroup):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _add_describe_option(self)


class Typer(typer.Typer):
    """A ``typer.Typer`` whose every command and sub-group carries the eager
    ``--describe`` flag. Use ``describe.Typer`` in place of ``typer.Typer``
    for the root app and every sub-app; ``configure()`` supplies the version
    and enrich hook.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("cls", DescribeGroup)
        super().__init__(*args, **kwargs)

    def command(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("cls", DescribeCommand)
        return super().command(*args, **kwargs)

    def add_typer(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("cls", DescribeGroup)
        return super().add_typer(*args, **kwargs)
