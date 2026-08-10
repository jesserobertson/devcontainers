from devtemplate.merge import merge_layer, merge_layer_keys, merge_layers
from devtemplate.schema import validate_devcontainer_config


def test_scalar_field_overlay_wins():
    base = {"name": "old"}
    overlay = {"name": "new"}
    assert merge_layer(base, overlay)["name"] == "new"


def test_untouched_base_fields_preserved():
    base = {"image": "ghcr.io/jesserobertson/base-ubuntu:latest"}
    overlay = {"features": {"a": {}}}
    merged = merge_layer(base, overlay)
    assert merged["image"] == "ghcr.io/jesserobertson/base-ubuntu:latest"


def test_features_union():
    base = {"features": {"a": {}}}
    overlay = {"features": {"b": {}}}
    merged = merge_layer(base, overlay)
    assert merged["features"] == {"a": {}, "b": {}}


def test_features_overlay_wins_on_collision():
    base = {"features": {"a": {"x": 1}}}
    overlay = {"features": {"a": {"x": 2}}}
    merged = merge_layer(base, overlay)
    assert merged["features"]["a"] == {"x": 2}


def test_mounts_concatenate_with_dedup():
    base = {"mounts": ["m1", "m2"]}
    overlay = {"mounts": ["m2", "m3"]}
    merged = merge_layer(base, overlay)
    assert merged["mounts"] == ["m1", "m2", "m3"]


def test_run_args_concatenate_without_dedup():
    base = {"runArgs": ["--gpus", "all"]}
    overlay = {"runArgs": ["--gpus", "all"]}
    merged = merge_layer(base, overlay)
    assert merged["runArgs"] == ["--gpus", "all", "--gpus", "all"]


def test_lifecycle_named_object_forms_union():
    base = {"postCreateCommand": {"x": "cmd1"}}
    overlay = {"postCreateCommand": {"y": "cmd2"}}
    merged = merge_layer(base, overlay)
    assert merged["postCreateCommand"] == {"x": "cmd1", "y": "cmd2"}


def test_lifecycle_non_object_form_overlay_replaces():
    base = {"postCreateCommand": {"x": "cmd1"}}
    overlay = {"postCreateCommand": "pixi install"}
    merged = merge_layer(base, overlay)
    assert merged["postCreateCommand"] == "pixi install"


def test_map_fields_merge():
    base = {"remoteEnv": {"A": "1"}}
    overlay = {"remoteEnv": {"B": "2"}}
    merged = merge_layer(base, overlay)
    assert merged["remoteEnv"] == {"A": "1", "B": "2"}


def test_unknown_field_overlay_wins():
    base = {"customSetting": "old"}
    overlay = {"customSetting": "new"}
    assert merge_layer(base, overlay)["customSetting"] == "new"


def test_add_agent_to_fastapi_scenario():
    base = {
        "name": "my-project",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/fastapi:latest": {}},
        "mounts": [
            "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume"
        ],
        "postCreateCommand": "pixi install",
        "remoteUser": "dev",
    }
    overlay = {
        "name": "agent",
        "image": "ghcr.io/jesserobertson/base-ubuntu:latest",
        "features": {"ghcr.io/jesserobertson/devcontainers/agent:latest": {}},
        "runArgs": ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"],
        "mounts": ["source=agent-pixi-cache,target=/home/dev/.cache/pixi,type=volume"],
        "postCreateCommand": "pixi install",
        "postStartCommand": "sudo /usr/local/bin/init-firewall.sh",
        "waitFor": "postStartCommand",
        "remoteUser": "dev",
    }
    merged = merge_layer(base, overlay)
    assert merged["features"] == {
        "ghcr.io/jesserobertson/devcontainers/fastapi:latest": {},
        "ghcr.io/jesserobertson/devcontainers/agent:latest": {},
    }
    assert merged["runArgs"] == ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]
    assert merged["postStartCommand"] == "sudo /usr/local/bin/init-firewall.sh"
    assert merged["waitFor"] == "postStartCommand"
    assert merged["mounts"] == [
        "source=my-project-pixi-cache,target=/home/dev/.cache/pixi,type=volume",
        "source=agent-pixi-cache,target=/home/dev/.cache/pixi,type=volume",
    ]
    validate_devcontainer_config(merged)


def test_merge_layer_keys_only_recomputes_requested_keys():
    layers = [
        {"image": "base", "remoteUser": "dev"},
        {"image": "override", "features": {"a": {}}},
    ]
    result = merge_layer_keys(layers, {"image"})
    assert result == {"image": "override"}


def test_merge_layer_keys_omits_key_no_layer_sets():
    layers = [{"remoteUser": "dev"}, {"features": {"a": {}}}]
    result = merge_layer_keys(layers, {"postStartCommand"})
    assert result == {}


def test_merge_layer_keys_respects_array_dedup_rule_scoped():
    layers = [{"mounts": ["m1"]}, {"mounts": ["m1", "m2"]}]
    result = merge_layer_keys(layers, {"mounts"})
    assert result == {"mounts": ["m1", "m2"]}


def test_merge_layer_keys_matches_merge_layers_for_full_key_set():
    layers = [
        {"image": "base", "features": {"a": {}}, "mounts": ["m1"]},
        {"image": "override", "features": {"b": {}}, "runArgs": ["--x"]},
    ]
    all_keys = {"image", "features", "mounts", "runArgs"}
    assert merge_layer_keys(layers, all_keys) == merge_layers(layers)
