from __future__ import annotations

import typer
from rich.console import Console
from typer.testing import CliRunner

from devtemplate.fuzzy import fuzzy_argument, resolve_or_confirm, resolve_or_create


def test_exact_match_passes_through_with_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    result = resolve_or_confirm("fastapi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_no_close_match_returns_err_listing_candidates():
    result = resolve_or_confirm(
        "zzz-nothing-like-it", ["fastapi", "agent"], label="feature"
    )
    assert result.is_err()
    error = str(result.unwrap_err())
    assert "fastapi" in error
    assert "agent" in error


def test_close_match_confirmed_yes_resolves(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_close_match_confirmed_no_returns_err(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_err()
    assert "fastpi" in str(result.unwrap_err())


def test_assume_yes_skips_the_prompt_entirely(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    result = resolve_or_confirm(
        "fastpi", ["fastapi", "agent"], label="feature", assume_yes=True
    )
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_non_interactive_close_match_fails_with_suggestion_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    result = resolve_or_confirm(
        "fastpi", ["fastapi", "agent"], label="feature", interactive=False
    )
    assert result.is_err()
    assert "fastapi" in str(result.unwrap_err())


def test_resolve_or_create_no_close_match_passes_through_unchanged():
    result = resolve_or_create("brand-new-name", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "brand-new-name"


def test_resolve_or_create_exact_match_passes_through_with_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    result = resolve_or_create("fastapi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_resolve_or_create_close_match_confirmed_yes_resolves(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = resolve_or_create("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_resolve_or_create_close_match_confirmed_no_returns_err(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    result = resolve_or_create("fastpi", ["fastapi", "agent"], label="feature")
    assert result.is_err()


def test_resolve_or_create_distinct_name_sharing_a_long_prefix_never_prompts(
    monkeypatch,
):
    # "base-cuda" vs "base-ubuntu" scores ~0.6 on difflib's ratio - a real
    # typo under resolve_or_confirm's looser cutoff, but here it's a
    # brand-new, legitimately distinct name that happens to share a
    # "base-" prefix (see resolve_or_create's docstring). Confirming a "did
    # you mean" prompt here would silently write to the wrong file, so this
    # must never prompt.
    monkeypatch.setattr(
        "typer.confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )
    result = resolve_or_create("base-cuda", ["base-ubuntu"], label="image")
    assert result.is_ok()
    assert result.unwrap() == "base-cuda"


runner = CliRunner()


def _greet_app():
    app = typer.Typer()
    console = Console()

    @app.command("greet")
    @fuzzy_argument(
        "names",
        candidates_fn=lambda settings: ["alice", "bob"],
        label="person",
        console=console,
    )
    def greet(
        names: list[str] = typer.Argument(..., help="Name(s) to greet."),  # noqa: B008
        json_output: bool = typer.Option(False, "--json", help="JSON mode."),
    ) -> None:
        for name in names:
            print(f"hello {name}")

    @app.command("noop")
    def _noop() -> None:
        """No-op command to prevent Typer single-command collapse in tests."""
        pass

    return app


def test_fuzzy_argument_exact_match_runs_unchanged():
    result = runner.invoke(_greet_app(), ["greet", "alice"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_injects_yes_flag_into_help():
    # Inspect the resolved Click command rather than rendered `--help` text:
    # Rich's help-panel layout is terminal-width sensitive and renders
    # differently on headless CI (Rich 15 on a runner with no tty collapses
    # the options panel), so asserting on the printed output is flaky. The
    # behaviour under test is just "the decorator adds a --yes/-y option".
    greet = typer.main.get_command(_greet_app()).commands["greet"]
    flags = {opt for param in greet.params for opt in getattr(param, "opts", [])}
    assert "--yes" in flags
    assert "-y" in flags


def test_fuzzy_argument_prompts_and_resolves_on_confirm():
    result = runner.invoke(_greet_app(), ["greet", "alise"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_yes_flag_skips_prompt():
    result = runner.invoke(_greet_app(), ["greet", "alise", "--yes"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output


def test_fuzzy_argument_json_mode_fails_with_suggestion_no_hang():
    result = runner.invoke(_greet_app(), ["greet", "alise", "--json"])
    assert result.exit_code == 1
    assert "alice" in result.output


def test_fuzzy_argument_resolves_every_item_in_a_multi_value_argument():
    result = runner.invoke(_greet_app(), ["greet", "alice", "bob"])
    assert result.exit_code == 0, result.output
    assert "hello alice" in result.output
    assert "hello bob" in result.output


def test_fuzzy_argument_collision_candidate_named_after_command():
    """Test that a candidate legitimately named 'greet' (the command name) is
    NOT silently dropped. This guards against regressions of the func.__name__
    workaround bug where ['greet', 'alice'] would incorrectly skip 'greet'."""

    def _collision_app():
        app = typer.Typer()
        console = Console()

        @app.command("greet")
        @fuzzy_argument(
            "names",
            candidates_fn=lambda settings: [
                "greet",
                "bob",
            ],  # "greet" is a real candidate
            label="person",
            console=console,
        )
        def greet(
            names: list[str] = typer.Argument(..., help="Name(s) to greet."),  # noqa: B008
            json_output: bool = typer.Option(False, "--json", help="JSON mode."),
        ) -> None:
            for name in names:
                print(f"hello {name}")

        @app.command("noop")
        def _noop() -> None:
            pass

        return app

    # Invoke the proper way (command name NOT in the list, since noop ensures Group)
    result = runner.invoke(_collision_app(), ["greet", "greet", "bob"])
    assert result.exit_code == 0, result.output
    assert "hello greet" in result.output
    assert "hello bob" in result.output
