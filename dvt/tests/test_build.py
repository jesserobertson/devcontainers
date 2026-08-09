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


def test_generate_dockerfile_installs_features_as_root():
    """The devcontainer Features spec requires install.sh to run as root
    regardless of the base image's own USER - a base image that ends on a
    non-root USER (e.g. USER dev) must not leak into the feature install RUN
    step, since install.sh commonly needs root (e.g. su-ing to _REMOTE_USER)."""
    content = generate_dockerfile(
        "ghcr.io/jesserobertson/base-ubuntu:latest",
        [("fastapi", "features/0-fastapi", {})],
    )
    lines = content.splitlines()
    copy_index = lines.index("COPY features/0-fastapi/ /tmp/dvt-feature/")
    run_index = next(
        i for i, line in enumerate(lines) if line.startswith("RUN chmod +x")
    )
    assert "USER root" in lines[copy_index + 1 : run_index]


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


def test_build_image_returns_err_when_copytree_destination_exists(tmp_path):
    feature_dir = tmp_path / "extracted"
    feature_dir.mkdir()
    (feature_dir / "install.sh").write_text("#!/bin/bash\n")
    scratch_dir = tmp_path / "scratch"

    fake_client = MagicMock()
    fake_client.images.build.return_value = (MagicMock(), iter([]))

    features = [("fastapi", feature_dir, {})]

    first_result = build_image(
        fake_client, "base:latest", features, "dvt/my-project:latest", scratch_dir
    )
    assert first_result.is_ok()

    # Reusing the same scratch_dir means shutil.copytree's destination already
    # exists, which raises FileExistsError (dirs_exist_ok defaults to False).
    second_result = build_image(
        fake_client, "base:latest", features, "dvt/my-project:latest", scratch_dir
    )

    assert second_result.is_err()
    assert isinstance(second_result.unwrap_err(), FileExistsError)


def test_build_image_returns_err_when_extracted_dir_missing(tmp_path):
    missing_feature_dir = tmp_path / "does-not-exist"
    scratch_dir = tmp_path / "scratch"

    fake_client = MagicMock()

    result = build_image(
        fake_client,
        "base:latest",
        [("fastapi", missing_feature_dir, {})],
        "dvt/my-project:latest",
        scratch_dir,
    )

    assert result.is_err()
    assert isinstance(result.unwrap_err(), FileNotFoundError)
    fake_client.images.build.assert_not_called()
