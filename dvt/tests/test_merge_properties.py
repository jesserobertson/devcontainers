from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from devtemplate.merge import merge_layer
from devtemplate.models import DevContainerConfig


@given(st.from_type(DevContainerConfig))
def test_merge_idempotent_except_run_args(config: DevContainerConfig) -> None:
    # runArgs intentionally concatenates without dedup (repeated flags like
    # --env-file are legitimate), so it is excluded from this idempotence
    # property and covered separately below.
    data = config.model_dump(exclude_defaults=True)
    merged = merge_layer(data, data)
    merged.pop("runArgs", None)
    comparable = dict(data)
    comparable.pop("runArgs", None)
    assert merged == comparable


def test_run_args_duplicates_on_repeat_by_design() -> None:
    data = {"runArgs": ["--gpus", "all"]}
    merged = merge_layer(data, data)
    assert merged["runArgs"] == ["--gpus", "all", "--gpus", "all"]


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_features_union_never_drops_a_key(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    merged_features = merge_layer(base_data, overlay_data).get("features", {})
    for key in base_data.get("features", {}):
        assert key in merged_features
    for key in overlay_data.get("features", {}):
        assert key in merged_features


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_run_args_concatenates_without_dropping(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    merged = merge_layer(base_data, overlay_data)
    expected_length = len(base_data.get("runArgs", [])) + len(
        overlay_data.get("runArgs", [])
    )
    assert len(merged.get("runArgs", [])) == expected_length


@given(st.from_type(DevContainerConfig), st.from_type(DevContainerConfig))
def test_merge_mounts_dedup_is_a_proper_set_union(
    base: DevContainerConfig, overlay: DevContainerConfig
) -> None:
    # Not "never produces duplicates": _merge_array_dedup only prevents overlay
    # from adding a *new* duplicate; it never cleans up duplicates base already
    # had (matching dev's original Rust behavior). base=["",""], overlay=[] is
    # a real, hypothesis-found counterexample to a naive no-duplicates claim.
    # The property that actually and unconditionally holds is set equality.
    base_data = base.model_dump(exclude_defaults=True)
    overlay_data = overlay.model_dump(exclude_defaults=True)
    merged_mounts = merge_layer(base_data, overlay_data).get("mounts", [])
    expected = set(base_data.get("mounts", [])) | set(overlay_data.get("mounts", []))
    assert set(merged_mounts) == expected
