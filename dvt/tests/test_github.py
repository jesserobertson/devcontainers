import httpx
import pytest

from devtemplate.github import fetch_template, list_template_names


def test_list_template_names_returns_only_directories():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"name": "fastapi", "type": "dir"},
                {"name": "README.md", "type": "file"},
                {"name": "agent", "type": "dir"},
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    names = list_template_names(client, "jesserobertson/devcontainers", "main")
    assert names == ["agent", "fastapi"]


def test_list_template_names_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        list_template_names(client, "jesserobertson/devcontainers", "main")


def test_fetch_template_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "fastapi", "image": "ghcr.io/x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    template = fetch_template(client, "jesserobertson/devcontainers", "main", "fastapi")
    assert template == {"name": "fastapi", "image": "ghcr.io/x"}


def test_fetch_template_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        fetch_template(client, "jesserobertson/devcontainers", "main", "nonexistent")
