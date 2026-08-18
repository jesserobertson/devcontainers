import httpx

from devtemplate.github import (
    fetch_image_metadata,
    fetch_template,
    list_image_names,
    list_template_names,
)


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
    result = list_template_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_ok()
    assert result.unwrap() == ["agent", "fastapi"]


def test_list_template_names_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = list_template_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)


def test_fetch_template_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "fastapi", "image": "ghcr.io/x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_template(client, "jesserobertson/devcontainers", "main", "fastapi")
    assert result.is_ok()
    assert result.unwrap() == {"name": "fastapi", "image": "ghcr.io/x"}


def test_fetch_template_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_template(
        client, "jesserobertson/devcontainers", "main", "nonexistent"
    )
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)


def test_list_image_names_returns_only_json_files():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"name": "base-ubuntu.json", "type": "file"},
                {"name": "README.md", "type": "file"},
                {"name": "base-cuda.json", "type": "file"},
                {"name": "subdir", "type": "dir"},
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = list_image_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_ok()
    assert result.unwrap() == ["base-cuda", "base-ubuntu"]


def test_list_image_names_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = list_image_names(client, "jesserobertson/devcontainers", "main")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)


def test_fetch_image_metadata_parses_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": "base-ubuntu",
                "description": "Ubuntu base.",
                "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
                "aliases": ["ubuntu"],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_image_metadata(
        client, "jesserobertson/devcontainers", "main", "base-ubuntu"
    )
    assert result.is_ok()
    assert result.unwrap()["ref"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_fetch_image_metadata_returns_err_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_image_metadata(
        client, "jesserobertson/devcontainers", "main", "nonexistent"
    )
    assert result.is_err()
    assert isinstance(result.unwrap_err(), httpx.HTTPStatusError)
