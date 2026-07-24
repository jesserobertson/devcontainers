import pytest
from pydantic import ValidationError

from devtemplate.config import Settings


def test_settings_default_github_source():
    settings = Settings()
    assert settings.github_repo == "jesserobertson/devcontainers"
    assert settings.github_branch == "main"


def test_settings_github_source_overridable_via_env(monkeypatch):
    monkeypatch.setenv("DVT_GITHUB_REPO", "someone/fork")
    monkeypatch.setenv("DVT_GITHUB_BRANCH", "dev")
    settings = Settings()
    assert settings.github_repo == "someone/fork"
    assert settings.github_branch == "dev"


def test_settings_paths_derive_from_data_dir(settings, tmp_path):
    assert settings.data_dir == tmp_path
    assert settings.templates_dir == tmp_path / "templates"
    assert settings.manifest_path == tmp_path / "manifest.json"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "no-slash",
        "too/many/slashes",
        "/missing-owner",
        "missing-repo/",
        "has space/repo",
    ],
)
def test_settings_rejects_malformed_github_repo(monkeypatch, value):
    monkeypatch.setenv("DVT_GITHUB_REPO", value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["", " ", "main ", " main", "\tmain"])
def test_settings_rejects_malformed_github_branch(monkeypatch, value):
    monkeypatch.setenv("DVT_GITHUB_BRANCH", value)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_accepts_well_formed_github_repo(monkeypatch):
    monkeypatch.setenv("DVT_GITHUB_REPO", "some-org_2/repo.name-2")
    assert Settings().github_repo == "some-org_2/repo.name-2"


def test_load_settings_returns_err_on_invalid_env(monkeypatch):
    from devtemplate.config import load_settings

    monkeypatch.setenv("DVT_GITHUB_REPO", "not-a-valid-repo-string")
    result = load_settings()
    assert result.is_err()


def test_load_settings_returns_ok_with_defaults():
    from devtemplate.config import load_settings

    result = load_settings()
    assert result.is_ok()
    assert result.unwrap().github_repo == "jesserobertson/devcontainers"
