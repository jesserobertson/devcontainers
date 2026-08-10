from __future__ import annotations

from typing import Any

SCALAR_FIELDS = {"name", "image", "remoteUser", "waitFor", "shutdownAction"}
LIFECYCLE_FIELDS = {
    "postCreateCommand",
    "postStartCommand",
    "postAttachCommand",
    "onCreateCommand",
    "updateContentCommand",
    "initializeCommand",
}
ARRAY_FIELDS = {"mounts", "forwardPorts"}
ARRAY_CONCAT_FIELDS = {"runArgs"}
MAP_FIELDS = {"remoteEnv", "containerEnv"}
FEATURE_FIELDS = {"features"}


def merge_layer(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge overlay onto base using field-type rules. Overlay is the higher-priority layer.

    Examples:
        >>> merge_layer({"image": "python:3.12"}, {"image": "python:3.13"})
        {'image': 'python:3.13'}

        >>> merge_layer({"forwardPorts": [8000]}, {"forwardPorts": [8000, 9000]})
        {'forwardPorts': [8000, 9000]}
    """
    result = dict(base)
    for key, overlay_value in overlay.items():
        if key in SCALAR_FIELDS:
            result[key] = overlay_value
        elif key in LIFECYCLE_FIELDS:
            result[key] = _merge_lifecycle_command(result.get(key), overlay_value)
        elif key in FEATURE_FIELDS:
            result[key] = _merge_feature_map(result.get(key), overlay_value)
        elif key in ARRAY_CONCAT_FIELDS:
            result[key] = _merge_array_concat(result.get(key), overlay_value)
        elif key in ARRAY_FIELDS:
            result[key] = _merge_array_dedup(result.get(key), overlay_value)
        elif key in MAP_FIELDS:
            result[key] = _merge_map(result.get(key), overlay_value)
        else:
            result[key] = overlay_value
    return result


def merge_layers(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Compose N layers in order (first = lowest priority, last = highest priority).

    Examples:
        >>> merge_layers([{"image": "a"}, {"image": "b"}, {}])
        {'image': 'b'}
    """
    result: dict[str, Any] = {}
    for layer in layers:
        result = merge_layer(result, layer)
    return result


def merge_layer_keys(layers: list[dict[str, Any]], keys: set[str]) -> dict[str, Any]:
    """Replay merge_layer's field-type rules across `layers` (lowest priority
    first), restricted to `keys`. Used by `feature remove` to recompute only
    the fields a removed feature's overlay touched, without re-merging (and
    so without risking a change to) anything else in the target file. A key
    in `keys` that no layer ever sets is simply absent from the result - the
    caller deletes it from the file rather than leaving an empty container.

    Examples:
        >>> merge_layer_keys([{"image": "a", "name": "x"}, {"image": "b"}], {"image"})
        {'image': 'b'}
    """
    result: dict[str, Any] = {}
    for layer in layers:
        filtered = {key: value for key, value in layer.items() if key in keys}
        result = merge_layer(result, filtered)
    return result


def _merge_lifecycle_command(base_value: Any, overlay_value: Any) -> Any:
    if isinstance(overlay_value, dict) and isinstance(base_value, dict):
        merged = dict(base_value)
        merged.update(overlay_value)
        return merged
    return overlay_value


def _merge_feature_map(base_value: Any, overlay_value: Any) -> Any:
    if not isinstance(overlay_value, dict):
        return base_value
    merged = dict(base_value) if isinstance(base_value, dict) else {}
    merged.update(overlay_value)
    return merged


def _merge_array_dedup(base_value: Any, overlay_value: Any) -> list[Any]:
    if not isinstance(overlay_value, list):
        return list(base_value) if isinstance(base_value, list) else []
    merged = list(base_value) if isinstance(base_value, list) else []
    for item in overlay_value:
        if item not in merged:
            merged.append(item)
    return merged


def _merge_array_concat(base_value: Any, overlay_value: Any) -> list[Any]:
    if not isinstance(overlay_value, list):
        return list(base_value) if isinstance(base_value, list) else []
    merged = list(base_value) if isinstance(base_value, list) else []
    merged.extend(overlay_value)
    return merged


def _merge_map(base_value: Any, overlay_value: Any) -> dict[str, Any]:
    if not isinstance(overlay_value, dict):
        return dict(base_value) if isinstance(base_value, dict) else {}
    merged = dict(base_value) if isinstance(base_value, dict) else {}
    merged.update(overlay_value)
    return merged
