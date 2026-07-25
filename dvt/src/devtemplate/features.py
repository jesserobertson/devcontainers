from __future__ import annotations

import hashlib
import re
import shutil
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from logerr import Err, Ok, Result

_WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')
_MANIFEST_ACCEPT = "application/vnd.oci.image.manifest.v1+json"


def _parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]:
    """Split 'registry/repository/path:tag' into (registry, repository_path, tag).

    E.g. 'ghcr.io/jesserobertson/devcontainers/fastapi:latest' ->
    ('ghcr.io', 'jesserobertson/devcontainers/fastapi', 'latest').
    """
    if "/" not in ref:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: expected registry/repository:tag")
        )
    registry, _, rest = ref.partition("/")
    if ":" not in rest:
        return Err(ValueError(f"Invalid feature ref {ref!r}: missing :tag"))
    repository, _, tag = rest.rpartition(":")
    if not repository or not tag:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: missing repository or tag")
        )
    return Ok((registry, repository, tag))


def _parse_www_authenticate(header_value: str) -> Result[dict[str, str], Exception]:
    params = dict(_WWW_AUTHENTICATE_PARAM.findall(header_value))
    if not {"realm", "service", "scope"} <= params.keys():
        return Err(
            ValueError(f"Unrecognized WWW-Authenticate header: {header_value!r}")
        )
    return Ok(params)


def _get_token(
    client: httpx.Client, registry: str, repository: str, tag: str
) -> Result[str, Exception]:
    """Anonymous OCI Distribution auth: probe the manifest endpoint unauthenticated,
    parse the resulting 401's WWW-Authenticate challenge, fetch a token from its
    realm. Registry-agnostic - not hardcoded to ghcr.io's own /token endpoint, since
    the realm/service/scope come from whatever registry actually answered."""
    try:
        probe = client.get(
            f"https://{registry}/v2/{repository}/manifests/{tag}",
            headers={"Accept": _MANIFEST_ACCEPT},
        )
    except Exception as exc:
        return Err(exc)
    if probe.status_code != 401:
        return Err(
            ValueError(
                f"Expected a 401 auth challenge from {registry}, got {probe.status_code}"
            )
        )
    challenge = probe.headers.get("www-authenticate")
    if challenge is None:
        return Err(
            ValueError(f"401 response from {registry} had no WWW-Authenticate header")
        )
    params_result = _parse_www_authenticate(challenge)
    if params_result.is_err():
        return Err(params_result.unwrap_err())
    params = params_result.unwrap()
    try:
        token_response = client.get(
            params["realm"],
            params={"service": params["service"], "scope": params["scope"]},
        )
        token_response.raise_for_status()
        return Ok(token_response.json()["token"])
    except Exception as exc:
        return Err(exc)


def _first_layer_digest(manifest: dict[str, Any], ref: str) -> Result[str, Exception]:
    """Pull the first layer's digest out of a manifest, treating any malformed
    shape (no layers, a non-dict layer entry, a missing/non-string digest) as a
    Result error rather than letting KeyError/TypeError escape to the caller."""
    layers = manifest.get("layers", [])
    if not layers:
        return Err(ValueError(f"Feature manifest for {ref!r} has no layers"))
    layer = layers[0]
    if not isinstance(layer, dict) or not isinstance(layer.get("digest"), str):
        return Err(
            ValueError(
                f"Feature manifest for {ref!r} has a malformed first layer "
                f"(missing or non-string digest): {layer!r}"
            )
        )
    return Ok(layer["digest"])


def _fetch_manifest(
    client: httpx.Client, registry: str, repository: str, tag: str, token: str
) -> Result[dict[str, Any], Exception]:
    try:
        response = client.get(
            f"https://{registry}/v2/{repository}/manifests/{tag}",
            headers={"Accept": _MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return Ok(response.json())
    except Exception as exc:
        return Err(exc)


def _fetch_and_extract_layer(
    client: httpx.Client,
    registry: str,
    repository: str,
    digest: str,
    token: str,
    dest_dir: Path,
) -> Result[Path, Exception]:
    """Fetch a Feature's tar layer blob and extract it. Follows redirects - GHCR
    (and most registries) 307-redirects blob downloads to a CDN URL, unlike
    manifest/token requests which respond directly. The blob is a plain POSIX tar
    despite the OCI annotation's *.tgz filename - not gzip-compressed.

    Extracts into a temporary sibling directory first and only renames it into
    place at dest_dir once extraction fully succeeds. This way a partial or
    corrupt extraction (bad tar, or extractall's filter="data" safety check
    rejecting a hostile archive) never leaves anything at dest_dir - so a later
    call for the same ref can't mistake a poisoned partial extraction for a
    valid cache hit.
    """
    try:
        response = client.get(
            f"https://{registry}/v2/{repository}/blobs/{digest}",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        )
        response.raise_for_status()
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(dir=dest_dir.parent))
        try:
            with tarfile.open(fileobj=BytesIO(response.content), mode="r:") as tar:
                tar.extractall(tmp_dir, filter="data")
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        tmp_dir.rename(dest_dir)
        return Ok(dest_dir)
    except Exception as exc:
        return Err(exc)


def pull_feature(
    client: httpx.Client, ref: str, cache_dir: Path
) -> Result[Path, Exception]:
    """Pull and extract an OCI Feature artifact, returning its extracted directory.

    Cached under cache_dir / sha256(ref) - hashed rather than derived from the ref's
    own text, since a ref can contain arbitrary registry/path characters that aren't
    all safe path segments, and (unlike this repo's own template names) Feature refs
    aren't restricted to a known-safe pattern - they can point at any registry.
    """
    ref_result = _parse_feature_ref(ref)
    if ref_result.is_err():
        return Err(ref_result.unwrap_err())
    registry, repository, tag = ref_result.unwrap()

    dest_dir = cache_dir / hashlib.sha256(ref.encode()).hexdigest()
    if dest_dir.exists():
        return Ok(dest_dir)

    token_result = _get_token(client, registry, repository, tag)
    if token_result.is_err():
        return Err(token_result.unwrap_err())
    token = token_result.unwrap()

    manifest_result = _fetch_manifest(client, registry, repository, tag, token)
    if manifest_result.is_err():
        return Err(manifest_result.unwrap_err())
    manifest = manifest_result.unwrap()

    digest_result = _first_layer_digest(manifest, ref)
    if digest_result.is_err():
        return Err(digest_result.unwrap_err())
    digest = digest_result.unwrap()

    return _fetch_and_extract_layer(
        client, registry, repository, digest, token, dest_dir
    )
