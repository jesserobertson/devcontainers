from __future__ import annotations

from unittest.mock import MagicMock

from devtemplate.workspace import resolve_existing, resolve_for_up


def _fake_client_with_workspaces(names: list[str]) -> MagicMock:
    client = MagicMock()
    client.containers.list.return_value = [
        MagicMock(labels={"dvt.workspace": name, "devcontainer.local_folder": "x"})
        for name in names
    ]
    return client


def test_resolve_for_up_passes_through_explicit_name_without_any_lookup(tmp_path):
    client = MagicMock()

    result = resolve_for_up(client, "explicit", tmp_path)

    assert result.unwrap() == "explicit"
    client.containers.list.assert_not_called()


def test_resolve_for_up_reuses_the_single_matching_workspace(tmp_path):
    client = _fake_client_with_workspaces(["my-custom-name"])

    result = resolve_for_up(client, None, tmp_path)

    assert result.unwrap() == "my-custom-name"


def test_resolve_for_up_falls_back_to_directory_name_when_no_match(tmp_path):
    client = _fake_client_with_workspaces([])

    result = resolve_for_up(client, None, tmp_path)

    assert result.unwrap() == tmp_path.resolve().name


def test_resolve_for_up_refuses_when_fallback_name_belongs_to_a_different_folder(
    tmp_path, monkeypatch
):
    client = _fake_client_with_workspaces([])
    other_container = MagicMock(
        labels={"devcontainer.local_folder": "/some/other/folder"}
    )
    monkeypatch.setattr(
        "devtemplate.workspace.lookup.find_workspace_container",
        lambda client, name: other_container,
    )

    result = resolve_for_up(client, None, tmp_path)

    assert result.is_err()
    message = str(result.unwrap_err())
    assert tmp_path.resolve().name in message
    assert "different folder" in message


def test_resolve_for_up_reuses_fallback_name_when_it_belongs_to_this_folder(
    tmp_path, monkeypatch
):
    client = _fake_client_with_workspaces([])
    own_container = MagicMock(
        labels={"devcontainer.local_folder": str(tmp_path.resolve())}
    )
    monkeypatch.setattr(
        "devtemplate.workspace.lookup.find_workspace_container",
        lambda client, name: own_container,
    )

    result = resolve_for_up(client, None, tmp_path)

    assert result.unwrap() == tmp_path.resolve().name


def test_resolve_for_up_refuses_on_multiple_matches(tmp_path):
    client = _fake_client_with_workspaces(["bar", "foo"])

    result = resolve_for_up(client, None, tmp_path)

    assert result.is_err()
    message = str(result.unwrap_err())
    assert "bar" in message
    assert "foo" in message
    assert "dvt up <name>" in message


def test_resolve_existing_passes_through_explicit_name_without_any_lookup(tmp_path):
    client = MagicMock()

    result = resolve_existing(client, "explicit", tmp_path, "ssh")

    assert result.unwrap() == "explicit"
    client.containers.list.assert_not_called()


def test_resolve_existing_uses_the_single_matching_workspace(tmp_path):
    client = _fake_client_with_workspaces(["my-custom-name"])

    result = resolve_existing(client, None, tmp_path, "ssh")

    assert result.unwrap() == "my-custom-name"


def test_resolve_existing_refuses_when_no_match(tmp_path):
    client = _fake_client_with_workspaces([])

    result = resolve_existing(client, None, tmp_path, "ssh")

    assert result.is_err()
    assert "No workspace found" in str(result.unwrap_err())


def test_resolve_existing_refuses_on_multiple_matches_naming_the_given_command(
    tmp_path,
):
    client = _fake_client_with_workspaces(["bar", "foo"])

    result = resolve_existing(client, None, tmp_path, "stop")

    assert result.is_err()
    message = str(result.unwrap_err())
    assert "bar" in message
    assert "foo" in message
    assert "dvt stop <name>" in message
