from __future__ import annotations

import hashlib
import shutil
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

__all__ = ["clear_pulled_features", "pull_feature"]


def clear_pulled_features(cache_dir: Path) -> None:
    """Delete every OCI Feature artifact `pull_feature` has ever extracted
    under cache_dir, so the next `dvt up` re-pulls each one from scratch.

    `pull_feature` caches by sha256(ref) forever once a ref has been pulled
    once (see its own docstring and test_pull_feature_is_cached_on_second_call)
    - correct for an immutable version tag, but a mutable tag like `:latest`
    (what every template in this repo references) can move upstream without
    that ever being noticed locally. There was previously no way to force a
    refresh short of deleting this directory by hand; `dvt feature sync`
    calls this precisely because "go get whatever's current" is already its
    whole purpose."""
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


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
