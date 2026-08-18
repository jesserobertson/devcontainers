import json

import httpx
import pytest

from devtemplate.images import (
    create_image_file,
    delete_image_file,
    find_repo_root,
    list_cached_images,
    load_cached_image,
    read_image_manifest,
    resolve_image_ref,
    sync_images,
    update_image_file,
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


def test_find_repo_root_finds_git_dir_in_an_ancestor(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "dvt" / "src"
    nested.mkdir(parents=True)

    result = find_repo_root(nested)

    assert result.is_ok()
    assert result.unwrap() == tmp_path


def test_find_repo_root_returns_err_when_no_git_dir_found(tmp_path):
    lonely = tmp_path / "no-git-anywhere-near-here"
    lonely.mkdir()

    result = find_repo_root(lonely)

    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_create_image_file_writes_expected_json(tmp_path):
    (tmp_path / ".git").mkdir()

    result = create_image_file(
        tmp_path,
        "base-ubuntu",
        ref="ghcr.io/jesserobertson/base-ubuntu:latest",
        description="Ubuntu base.",
        aliases=["ubuntu", "default"],
    )

    assert result.is_ok()
    written = json.loads((tmp_path / "images" / "base-ubuntu.json").read_text())
    assert written == {
        "name": "base-ubuntu",
        "description": "Ubuntu base.",
        "ref": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "aliases": ["ubuntu", "default"],
    }


def test_create_image_file_refuses_when_already_exists(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = create_image_file(
        tmp_path, "base-ubuntu", ref="x", description="y", aliases=[]
    )

    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileExistsError)


def test_update_image_file_edits_only_given_fields(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text(
        json.dumps(
            {
                "name": "base-ubuntu",
                "description": "old description",
                "ref": "ghcr.io/x/base-ubuntu:latest",
                "aliases": ["ubuntu"],
            }
        )
    )

    result = update_image_file(tmp_path, "base-ubuntu", description="new description")

    assert result.is_ok()
    written = json.loads((images_dir / "base-ubuntu.json").read_text())
    assert written["description"] == "new description"
    assert written["ref"] == "ghcr.io/x/base-ubuntu:latest"
    assert written["aliases"] == ["ubuntu"]


def test_update_image_file_refuses_when_missing(tmp_path):
    result = update_image_file(tmp_path, "nonexistent", description="x")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def test_delete_image_file_removes_the_file(tmp_path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    (images_dir / "base-ubuntu.json").write_text('{"name": "base-ubuntu"}')

    result = delete_image_file(tmp_path, "base-ubuntu")

    assert result.is_ok()
    assert not (images_dir / "base-ubuntu.json").exists()


def test_delete_image_file_refuses_when_missing(tmp_path):
    result = delete_image_file(tmp_path, "nonexistent")
    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)


def _write_image(settings, name, ref, aliases=None):
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    (settings.images_dir / f"{name}.json").write_text(
        json.dumps(
            {"name": name, "description": "", "ref": ref, "aliases": aliases or []}
        )
    )


def test_resolve_image_ref_passthrough_on_empty_cache(settings):
    result = resolve_image_ref("anything", settings)
    assert result.is_ok()
    assert result.unwrap() == "anything"


def test_resolve_image_ref_exact_ref_passes_through(settings):
    _write_image(settings, "base-ubuntu", "ghcr.io/x/base-ubuntu:latest")
    result = resolve_image_ref("ghcr.io/x/base-ubuntu:latest", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-ubuntu:latest"


def test_resolve_image_ref_exact_name_resolves_to_ref(settings):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    result = resolve_image_ref("base-cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_exact_alias_resolves_to_ref(settings):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest", aliases=["cuda", "gpu"])
    result = resolve_image_ref("cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_no_close_match_passes_through_as_literal(settings):
    _write_image(settings, "base-ubuntu", "ghcr.io/x/base-ubuntu:latest")
    result = resolve_image_ref("myregistry.example.com/custom:latest", settings)
    assert result.is_ok()
    assert result.unwrap() == "myregistry.example.com/custom:latest"


def test_resolve_image_ref_close_typo_confirmed_yes_resolves(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr("typer.confirm", lambda *a, **k: True)
    result = resolve_image_ref("bas-cuda", settings)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_close_typo_confirmed_no_returns_err(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr("typer.confirm", lambda *a, **k: False)
    result = resolve_image_ref("bas-cuda", settings)
    assert result.is_err()


def test_resolve_image_ref_assume_yes_skips_prompt(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    result = resolve_image_ref("bas-cuda", settings, assume_yes=True)
    assert result.is_ok()
    assert result.unwrap() == "ghcr.io/x/base-cuda:latest"


def test_resolve_image_ref_non_interactive_close_typo_fails_with_suggestion(settings, monkeypatch):
    _write_image(settings, "base-cuda", "ghcr.io/x/base-cuda:latest")
    monkeypatch.setattr(
        "typer.confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no prompt"))
    )
    result = resolve_image_ref("bas-cuda", settings, interactive=False)
    assert result.is_err()
    assert "base-cuda" in str(result.unwrap_err())
