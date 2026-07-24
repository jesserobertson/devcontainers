import httpx
import pytest

from devtemplate.store import (
    list_cached_templates,
    load_cached_template,
    read_manifest,
    sync_templates,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_writes_templates_and_manifest(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/templates"):
            return httpx.Response(200, json=[{"name": "fastapi", "type": "dir"}])
        return httpx.Response(200, json={"name": "fastapi", "image": "ghcr.io/x"})

    result = sync_templates(settings, _client(handler))

    assert result.is_ok()
    assert result.unwrap() == ["fastapi"]
    assert list_cached_templates(settings) == ["fastapi"]

    loaded = load_cached_template(settings, "fastapi")
    assert loaded.is_ok()
    assert loaded.unwrap() == {"name": "fastapi", "image": "ghcr.io/x"}

    manifest = read_manifest(settings)
    assert manifest.is_ok()
    assert manifest.unwrap() == ["fastapi"]


def test_sync_does_not_touch_custom_template_dirs(settings):
    custom_dir = settings.templates_dir / "my-custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "devcontainer.json").write_text('{"name": "custom"}')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/templates"):
            return httpx.Response(200, json=[{"name": "fastapi", "type": "dir"}])
        return httpx.Response(200, json={"name": "fastapi"})

    result = sync_templates(settings, _client(handler))

    assert result.is_ok()
    assert (custom_dir / "devcontainer.json").read_text() == '{"name": "custom"}'
    assert "my-custom" not in read_manifest(settings).unwrap()


def test_load_cached_template_missing_returns_err(settings):
    result = load_cached_template(settings, "nonexistent")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_list_cached_templates_empty_before_sync(settings):
    assert list_cached_templates(settings) == []


@pytest.mark.parametrize(
    "name", ["..", "has space", "UPPERCASE", "-leading-dash", "has_underscore", ""]
)
def test_load_cached_template_rejects_invalid_name(settings, name):
    result = load_cached_template(settings, name)
    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)


def test_sync_rejects_malicious_template_name_from_github(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/templates"):
            return httpx.Response(200, json=[{"name": "..", "type": "dir"}])
        return httpx.Response(200, json={"name": "escape"})

    result = sync_templates(settings, _client(handler))

    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)
    assert list_cached_templates(settings) == []
