import httpx

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
