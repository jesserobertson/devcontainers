from __future__ import annotations

import hashlib
import io
import tarfile

import httpx

from devtemplate.features import pull_feature
from devtemplate.features.oci import parse_feature_ref


def _make_feature_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content in (
            ("devcontainer-feature.json", b'{"id": "fastapi", "version": "1.0.0"}'),
            ("install.sh", b"#!/bin/bash\necho installed\n"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _registry_handler(blob_digest: str, blob_bytes: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path.endswith("/manifests/latest")
            and "authorization" not in request.headers
        ):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",'
                        'service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith("/manifests/latest"):
            return httpx.Response(
                200,
                json={
                    "schemaVersion": 2,
                    "layers": [
                        {
                            "mediaType": "application/vnd.devcontainers.layer.v1+tar",
                            "digest": blob_digest,
                            "size": len(blob_bytes),
                        }
                    ],
                },
            )
        if path.endswith(f"/blobs/{blob_digest}"):
            return httpx.Response(200, content=blob_bytes)
        return httpx.Response(404)

    return handler


def testparse_feature_ref_splits_registry_repository_tag():
    result = parse_feature_ref("ghcr.io/jesserobertson/devcontainers/fastapi:latest")
    assert result.is_ok()
    assert result.unwrap() == (
        "ghcr.io",
        "jesserobertson/devcontainers/fastapi",
        "latest",
    )


def testparse_feature_ref_rejects_missing_tag():
    result = parse_feature_ref("ghcr.io/jesserobertson/devcontainers/fastapi")
    assert result.is_err()


def test_pull_feature_extracts_devcontainer_feature_json_and_install_sh(tmp_path):
    blob = _make_feature_tar()
    handler = _registry_handler("sha256:deadbeef", blob)
    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_ok()
    extracted = result.unwrap()
    assert (extracted / "devcontainer-feature.json").exists()
    assert (extracted / "install.sh").read_text() == "#!/bin/bash\necho installed\n"


def test_pull_feature_is_cached_on_second_call(tmp_path):
    blob = _make_feature_tar()
    call_count = {"n": 0}

    def counting_handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return _registry_handler("sha256:deadbeef", blob)(request)

    client = httpx.Client(transport=httpx.MockTransport(counting_handler))
    ref = "ghcr.io/jesserobertson/devcontainers/fastapi:latest"

    first = pull_feature(client, ref, tmp_path)
    calls_after_first = call_count["n"]
    second = pull_feature(client, ref, tmp_path)

    assert first.is_ok() and second.is_ok()
    assert first.unwrap() == second.unwrap()
    assert call_count["n"] == calls_after_first  # no new HTTP calls on cache hit


def test_pull_feature_returns_err_on_manifest_404(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "authorization" not in request.headers and request.url.path.endswith(
            "/manifests/latest"
        ):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/missing:pull"'
                    )
                },
            )
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/missing:latest", tmp_path
    )

    assert result.is_err()


def test_pull_feature_returns_err_on_manifest_layer_missing_digest(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path.endswith("/manifests/latest")
            and "authorization" not in request.headers
        ):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",'
                        'service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith("/manifests/latest"):
            return httpx.Response(
                200,
                json={
                    "schemaVersion": 2,
                    "layers": [
                        {"mediaType": "application/vnd.devcontainers.layer.v1+tar"}
                    ],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_err()


def test_pull_feature_does_not_poison_cache_on_corrupt_tar_blob(tmp_path):
    corrupt_blob = b"this is not a valid tar archive at all"
    handler = _registry_handler("sha256:corrupt", corrupt_blob)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ref = "ghcr.io/jesserobertson/devcontainers/fastapi:latest"

    result = pull_feature(client, ref, tmp_path)

    assert result.is_err()
    dest_dir = tmp_path / hashlib.sha256(ref.encode()).hexdigest()
    assert not dest_dir.exists()


def test_pull_feature_returns_err_on_probe_network_failure(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_err()


def test_pull_feature_returns_err_on_non_dict_manifest(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path.endswith("/manifests/latest")
            and "authorization" not in request.headers
        ):
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": (
                        'Bearer realm="https://ghcr.io/token",'
                        'service="ghcr.io",'
                        'scope="repository:jesserobertson/devcontainers/fastapi:pull"'
                    )
                },
            )
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith("/manifests/latest"):
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_err()


def test_get_token_returns_err_on_malformed_www_authenticate_header(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "authorization" not in request.headers and request.url.path.endswith(
            "/manifests/latest"
        ):
            return httpx.Response(
                401,
                headers={"www-authenticate": 'Bearer error="insufficient_scope"'},
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = pull_feature(
        client, "ghcr.io/jesserobertson/devcontainers/fastapi:latest", tmp_path
    )

    assert result.is_err()


def testparse_feature_ref_accepts_digest_form():
    result = parse_feature_ref(
        "ghcr.io/jesserobertson/devcontainers/fastapi@sha256:"
        "f26cbb9c85b8211fa150e50200d48033d82d6678b6c871e8c2db015a1d81ffff"
    )
    assert result.is_ok()
    registry, repository, reference = result.unwrap()
    assert registry == "ghcr.io"
    assert repository == "jesserobertson/devcontainers/fastapi"
    assert reference == (
        "sha256:f26cbb9c85b8211fa150e50200d48033d82d6678b6c871e8c2db015a1d81ffff"
    )


def testparse_feature_ref_rejects_digest_form_missing_repository():
    result = parse_feature_ref("ghcr.io/@sha256:abc")
    assert result.is_err()


def test_pull_feature_works_with_digest_ref(tmp_path):
    blob = _make_feature_tar()
    digest = "sha256:deadbeef"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (
            path.endswith(f"/manifests/{digest}")
            and "authorization" not in request.headers
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
        if path == "/token":
            return httpx.Response(200, json={"token": "fake-token"})
        if path.endswith(f"/manifests/{digest}"):
            return httpx.Response(
                200,
                json={
                    "layers": [
                        {
                            "mediaType": "application/vnd.devcontainers.layer.v1+tar",
                            "digest": "sha256:layerdigest",
                            "size": len(blob),
                        }
                    ]
                },
            )
        if path.endswith("/blobs/sha256:layerdigest"):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = pull_feature(
        client, f"ghcr.io/jesserobertson/devcontainers/fastapi@{digest}", tmp_path
    )

    assert result.is_ok()
    assert (result.unwrap() / "devcontainer-feature.json").exists()
