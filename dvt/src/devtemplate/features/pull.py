from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
from logerr.utilities import wrap_result

from devtemplate.features.oci import (
    fetch_and_extract_layer,
    fetch_manifest,
    first_layer_digest,
    get_token,
    parse_feature_ref,
)

__all__ = ["pull_feature"]


@wrap_result
def pull_feature(client: httpx.Client, ref: str, cache_dir: Path) -> Path:
    """Pull and extract an OCI Feature artifact, returning its extracted directory.

    Cached under cache_dir / sha256(ref) - hashed rather than derived from the ref's
    own text, since a ref can contain arbitrary registry/path characters that aren't
    all safe path segments, and (unlike this repo's own template names) Feature refs
    aren't restricted to a known-safe pattern - they can point at any registry.
    """
    registry, repository, tag = parse_feature_ref(ref).unwrap()

    dest_dir = cache_dir / hashlib.sha256(ref.encode()).hexdigest()
    if dest_dir.exists():
        return dest_dir

    token = get_token(client, registry, repository, tag).unwrap()
    manifest = fetch_manifest(client, registry, repository, tag, token).unwrap()
    digest = first_layer_digest(manifest, ref).unwrap()

    return fetch_and_extract_layer(
        client, registry, repository, digest, token, dest_dir
    ).unwrap()
