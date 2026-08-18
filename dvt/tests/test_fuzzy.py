from __future__ import annotations

from devtemplate.fuzzy import resolve_or_confirm


def test_exact_match_passes_through_with_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastapi", ["fastapi", "agent"], label="feature")
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_no_close_match_returns_err_listing_candidates():
    result = resolve_or_confirm("zzz-nothing-like-it", ["fastapi", "agent"], label="feature")
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
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature", assume_yes=True)
    assert result.is_ok()
    assert result.unwrap() == "fastapi"


def test_non_interactive_close_match_fails_with_suggestion_no_prompt(monkeypatch):
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt"))
    )
    result = resolve_or_confirm("fastpi", ["fastapi", "agent"], label="feature", interactive=False)
    assert result.is_err()
    assert "fastapi" in str(result.unwrap_err())
