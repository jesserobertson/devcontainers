from __future__ import annotations

from unittest.mock import MagicMock

from devtemplate.build import build_image, generate_dockerfile


def test_generate_dockerfile_no_features():
    content = generate_dockerfile("ghcr.io/jesserobertson/base-ubuntu:latest", [])
    assert content == (
        "FROM ghcr.io/jesserobertson/base-ubuntu:latest AS stage0\n"
        "FROM stage0 AS final\n"
    )


def test_generate_dockerfile_single_feature():
    content = generate_dockerfile(
        "ghcr.io/jesserobertson/base-ubuntu:latest",
        [("fastapi", "features/0-fastapi", {})],
    )
    assert "FROM ghcr.io/jesserobertson/base-ubuntu:latest AS stage0" in content
    assert "FROM stage0 AS feature-0-fastapi" in content
    assert "COPY features/0-fastapi/ /tmp/dvt-feature/" in content
    assert "/tmp/dvt-feature/install.sh" in content
    assert "FROM feature-0-fastapi AS final" in content


def test_generate_dockerfile_quotes_option_values_safely():
    content = generate_dockerfile(
        "base:latest",
        [("ollama", "features/0-ollama", {"model": "llama3.2; rm -rf /"})],
    )
    assert "MODEL='llama3.2; rm -rf /'" in content


def test_build_image_writes_dockerfile_and_copies_features(tmp_path):
    feature_dir = tmp_path / "extracted"
    feature_dir.mkdir()
    (feature_dir / "install.sh").write_text("#!/bin/bash\n")
    scratch_dir = tmp_path / "scratch"

    fake_client = MagicMock()
    fake_client.images.build.return_value = (MagicMock(), iter([]))

    result = build_image(
        fake_client,
        "base:latest",
        [("fastapi", feature_dir, {})],
        "dvt/my-project:latest",
        scratch_dir,
    )

    assert result.is_ok()
    assert result.unwrap() == "dvt/my-project:latest"
    assert (scratch_dir / "Dockerfile").exists()
    assert (scratch_dir / "features" / "0-fastapi" / "install.sh").exists()
    fake_client.images.build.assert_called_once()
    _, kwargs = fake_client.images.build.call_args
    assert kwargs["tag"] == "dvt/my-project:latest"
    assert kwargs["path"] == str(scratch_dir)


def test_build_image_returns_err_on_build_failure(tmp_path):
    fake_client = MagicMock()
    fake_client.images.build.side_effect = RuntimeError("build failed")

    result = build_image(
        fake_client, "base:latest", [], "dvt/x:latest", tmp_path / "scratch"
    )

    assert result.is_err()
