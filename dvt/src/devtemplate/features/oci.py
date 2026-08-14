from __future__ import annotations

import re
import shutil
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from logerr import Err, Ok, Result
from logerr.utilities import wrap_result

__all__ = [
    "parse_feature_ref",
    "parse_www_authenticate",
    "get_token",
    "fetch_manifest",
    "first_layer_digest",
    "fetch_and_extract_layer",
]

WWW_AUTHENTICATE_PARAM = re.compile(r'(\w+)="([^"]*)"')
MANIFEST_ACCEPT = "application/vnd.oci.image.manifest.v1+json"


def parse_feature_ref(ref: str) -> Result[tuple[str, str, str], Exception]:
    """Split 'registry/repository/path:tag' or 'registry/repository/path@sha256:hex'
    into (registry, repository_path, reference), where reference is either a tag
    or a digest - OCI registries accept either directly in the manifest URL's
    final path segment, so no other code in this module needs to distinguish
    them. Digest form is checked first: 'sha256:hex' itself contains a colon,
    so checking for '@sha256:' before falling through to the tag-splitting
    rpartition avoids misparsing a digest ref as a malformed tag ref.
    """
    if "/" not in ref:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: expected registry/repository:tag")
        )
    registry, _, rest = ref.partition("/")
    if "@sha256:" in rest:
        repository, _, digest = rest.partition("@")
        if not repository or not digest:
            return Err(
                ValueError(f"Invalid feature ref {ref!r}: missing repository or digest")
            )
        return Ok((registry, repository, digest))
    if ":" not in rest:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: missing :tag or @sha256:digest")
        )
    repository, _, tag = rest.rpartition(":")
    if not repository or not tag:
        return Err(
            ValueError(f"Invalid feature ref {ref!r}: missing repository or tag")
        )
    return Ok((registry, repository, tag))


def parse_www_authenticate(header_value: str) -> Result[dict[str, str], Exception]:
    params = dict(WWW_AUTHENTICATE_PARAM.findall(header_value))
    return Result.from_predicate(
        params,
        lambda p: {"realm", "service", "scope"} <= p.keys(),
        ValueError(f"Unrecognized WWW-Authenticate header: {header_value!r}"),
    )


@wrap_result
def get_token(client: httpx.Client, registry: str, repository: str, tag: str) -> str:
    """Anonymous OCI Distribution auth: probe the manifest endpoint unauthenticated,
    parse the resulting 401's WWW-Authenticate challenge, fetch a token from its
    realm. Registry-agnostic - not hardcoded to ghcr.io's own /token endpoint, since
    the realm/service/scope come from whatever registry actually answered."""
    probe = client.get(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={"Accept": MANIFEST_ACCEPT},
    )
    if probe.status_code != 401:
        raise ValueError(
            f"Expected a 401 auth challenge from {registry}, got {probe.status_code}"
        )
    challenge = probe.headers.get("www-authenticate")
    if challenge is None:
        raise ValueError(f"401 response from {registry} had no WWW-Authenticate header")
    params = parse_www_authenticate(challenge).unwrap()
    token_response = client.get(
        params["realm"],
        params={"service": params["service"], "scope": params["scope"]},
    )
    token_response.raise_for_status()
    return str(token_response.json()["token"])


def first_layer_digest(manifest: Any, ref: str) -> Result[str, Exception]:
    """Pull the first layer's digest out of a manifest, treating any malformed
    shape (a non-dict manifest, no layers, a non-dict layer entry, a
    missing/non-string digest) as a Result error rather than letting
    AttributeError/KeyError/TypeError escape to the caller."""
    if not isinstance(manifest, dict):
        return Err(
            ValueError(
                f"Feature manifest for {ref!r} is not a JSON object: {manifest!r}"
            )
        )
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


@wrap_result
def fetch_manifest(
    client: httpx.Client, registry: str, repository: str, tag: str, token: str
) -> Any:
    """Returns whatever JSON value the registry responded with - not
    necessarily a dict. first_layer_digest is responsible for rejecting a
    non-dict manifest as a Result error rather than trusting this shape."""
    response = client.get(
        f"https://{registry}/v2/{repository}/manifests/{tag}",
        headers={"Accept": MANIFEST_ACCEPT, "Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()


@wrap_result
def fetch_and_extract_layer(
    client: httpx.Client,
    registry: str,
    repository: str,
    digest: str,
    token: str,
    dest_dir: Path,
) -> Path:
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
    return dest_dir
