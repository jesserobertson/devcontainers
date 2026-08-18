import json

import httpx
import pytest

from devtemplate.images import (
    list_cached_images,
    load_cached_image,
    read_image_manifest,
    sync_images,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_writes_images_and_manifest(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(
            200,
            json={"name": "base-ubuntu", "ref": "ghcr.io/x/base-ubuntu:latest"},
        )

    result = sync_images(settings, _client(handler))

    assert result.is_ok()
    assert result.unwrap() == ["base-ubuntu"]
    assert list_cached_images(settings) == ["base-ubuntu"]

    loaded = load_cached_image(settings, "base-ubuntu")
    assert loaded.is_ok()
    assert loaded.unwrap() == {"name": "base-ubuntu", "ref": "ghcr.io/x/base-ubuntu:latest"}

    manifest = read_image_manifest(settings)
    assert manifest.is_ok()
    assert manifest.unwrap() == ["base-ubuntu"]


def test_sync_does_not_touch_custom_image_files(settings):
    settings.images_dir.mkdir(parents=True)
    (settings.images_dir / "my-custom.json").write_text('{"name": "my-custom"}')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(200, json={"name": "base-ubuntu"})

    result = sync_images(settings, _client(handler))

    assert result.is_ok()
    assert (settings.images_dir / "my-custom.json").read_text() == '{"name": "my-custom"}'
    assert "my-custom" not in read_image_manifest(settings).unwrap()


def test_load_cached_image_missing_returns_err(settings):
    result = load_cached_image(settings, "nonexistent")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_list_cached_images_empty_before_sync(settings):
    assert list_cached_images(settings) == []


@pytest.mark.parametrize(
    "name", ["..", "has space", "UPPERCASE", "-leading-dash", "has_underscore", ""]
)
def test_load_cached_image_rejects_invalid_name(settings, name):
    result = load_cached_image(settings, name)
    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)


def test_sync_prunes_images_removed_upstream(settings):
    def handler_v1(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(
                200,
                json=[
                    {"name": "base-ubuntu.json", "type": "file"},
                    {"name": "old-image.json", "type": "file"},
                ],
            )
        if request.url.path.endswith("old-image.json"):
            return httpx.Response(200, json={"name": "old-image"})
        return httpx.Response(200, json={"name": "base-ubuntu"})

    first = sync_images(settings, _client(handler_v1))
    assert first.is_ok()
    assert set(list_cached_images(settings)) == {"base-ubuntu", "old-image"}

    def handler_v2(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "base-ubuntu.json", "type": "file"}])
        return httpx.Response(200, json={"name": "base-ubuntu"})

    second = sync_images(settings, _client(handler_v2))

    assert second.is_ok()
    assert list_cached_images(settings) == ["base-ubuntu"]


def test_sync_rejects_malicious_image_name_from_github(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/images"):
            return httpx.Response(200, json=[{"name": "...json", "type": "file"}])
        return httpx.Response(200, json={"name": "escape"})

    result = sync_images(settings, _client(handler))

    assert result.is_err()
    assert isinstance(result.unwrap_err(), ValueError)
    assert list_cached_images(settings) == []
