"""Guards against `dvt --help` regressing: every command, group, argument and
option in the CLI tree must carry non-empty help text, so nothing is added
later without being documented.
"""

from __future__ import annotations

import typer

from devtemplate.cli import app

root = typer.main.get_command(app)


def _iter_commands(command, path: str):
    # Typer vendors its own click fork (typer._click), so `command` is a
    # TyperGroup/TyperCommand rather than a stdlib click.Group/click.Command
    # instance - isinstance checks against the public `click` package silently
    # never match. Duck-type on `.commands` instead of isinstance(..., click.Group).
    yield path, command
    for name, sub in getattr(command, "commands", {}).items():
        yield from _iter_commands(sub, f"{path} {name}")


def _commands():
    return list(_iter_commands(root, "dvt"))


def test_every_command_has_help_text():
    missing = [
        path for path, command in _commands() if not (command.help or "").strip()
    ]
    assert not missing, f"Commands missing help text: {missing}"


def test_every_argument_and_option_has_help_text():
    missing = []
    for path, command in _commands():
        for param in command.params:
            if not hasattr(param, "help"):
                continue
            if not (getattr(param, "help", None) or "").strip():
                missing.append(f"{path} {param.name!r}")
    assert not missing, f"Arguments/options missing help text: {missing}"
