from __future__ import annotations

import io
import tarfile

import httpx

from devtemplate.features.oci import fetch_and_extract_layer, fetch_manifest, get_token

MANIFEST_JSON = {
    "schemaVersion": 2,
    "layers": [
        {
            "mediaType": "application/vnd.devcontainers.layer.v1+tar",
            "digest": "sha256:deadbeef",
            "size": 123,
        }
    ],
}


def _make_tar_bytes() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        content = b"#!/bin/bash\necho installed\n"
        info = tarfile.TarInfo(name="install.sh")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_get_token_retries_past_a_transient_failure_and_succeeds():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("simulated transient network failure")
        if "authorization" not in request.headers and request.url.path.endswith(
            "/manifests/latest"
        ):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = get_token(
        client, "ghcr.io", "jesserobertson/devcontainers/fastapi", "latest"
    )

    assert result.is_ok()
    assert result.unwrap() == "fake-token"
    assert call_count["n"] > 1  # the first (failing) attempt really happened


def test_fetch_manifest_retries_past_a_transient_failure_and_succeeds():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("simulated transient network failure")
        return httpx.Response(200, json=MANIFEST_JSON)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = fetch_manifest(
        client,
        "ghcr.io",
        "jesserobertson/devcontainers/fastapi",
        "latest",
        "fake-token",
    )

    assert result.is_ok()
    assert result.unwrap() == MANIFEST_JSON
    assert call_count["n"] > 1


def test_fetch_and_extract_layer_retries_past_a_transient_failure_and_succeeds(
    tmp_path,
):
    blob = _make_tar_bytes()
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise httpx.ConnectError("simulated transient network failure")
        return httpx.Response(200, content=blob)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dest = tmp_path / "extracted"

    result = fetch_and_extract_layer(
        client,
        "ghcr.io",
        "jesserobertson/devcontainers/fastapi",
        "sha256:deadbeef",
        "fake-token",
        dest,
    )

    assert result.is_ok()
    assert (dest / "install.sh").read_text() == "#!/bin/bash\necho installed\n"
    assert call_count["n"] > 1
