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
