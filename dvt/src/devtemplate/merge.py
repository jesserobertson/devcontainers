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
    """Merge overlay onto base using field-type rules. Overlay is the higher-priority layer."""
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
    """Compose N layers in order (first = lowest priority, last = highest priority)."""
    result: dict[str, Any] = {}
    for layer in layers:
        result = merge_layer(result, layer)
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
