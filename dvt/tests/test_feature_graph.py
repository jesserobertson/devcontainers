from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from devtemplate.feature_graph import (
    load_cached_specs,
    normalise_ref,
    read_feature_spec,
    ref_to_id,
    resolve_feature_graph,
)


@dataclass
class FakeRegistry:
    """Builds devcontainer-feature.json fixture dirs on disk and hands
    resolve_feature_graph a `pull` callback that returns them, recording every
    ref it was asked for so tests can assert what was (and wasn't) pulled."""

    root: Path
    specs: dict[str, dict] = field(default_factory=dict)
    pulled: list[str] = field(default_factory=list)

    def add(
        self,
        ref: str,
        *,
        id: str,
        depends_on: object = None,
        installs_after: object = None,
        container_env: object = None,
    ) -> None:
        spec: dict = {"id": id}
        if depends_on is not None:
            spec["dependsOn"] = depends_on
        if installs_after is not None:
            spec["installsAfter"] = installs_after
        if container_env is not None:
            spec["containerEnv"] = container_env
        self.specs[normalise_ref(ref)] = spec

    def pull(self, ref: str) -> Path:
        self.pulled.append(ref)
        if ref not in self.specs:
            raise FileNotFoundError(f"registry 404 for {ref!r}")
        dest = self.root / hashlib.sha256(ref.encode()).hexdigest()
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "devcontainer-feature.json").write_text(json.dumps(self.specs[ref]))
        (dest / "install.sh").write_text("#!/bin/sh\n")
        return dest


@pytest.fixture
def reg(tmp_path: Path) -> FakeRegistry:
    return FakeRegistry(tmp_path)


def test_normalise_ref_appends_latest_when_tagless():
    assert (
        normalise_ref("ghcr.io/devcontainers/features/pixi")
        == "ghcr.io/devcontainers/features/pixi:latest"
    )


def test_normalise_ref_leaves_tagged_ref_untouched():
    assert normalise_ref("ghcr.io/x/pixi:1.2.3") == "ghcr.io/x/pixi:1.2.3"


def test_normalise_ref_leaves_digest_ref_untouched():
    ref = "ghcr.io/x/pixi@sha256:abcdef"
    assert normalise_ref(ref) == ref


def test_read_feature_spec_defaults_when_fields_absent(tmp_path: Path):
    (tmp_path / "devcontainer-feature.json").write_text('{"id": "solo"}')
    spec = read_feature_spec(tmp_path)
    assert spec.id == "solo"
    assert spec.depends_on == ()
    assert spec.installs_after == ()
    assert spec.container_env == {}


def test_read_feature_spec_normalises_depends_on_object_form_to_sorted_refs(
    tmp_path: Path,
):
    (tmp_path / "devcontainer-feature.json").write_text(
        json.dumps(
            {
                "id": "x",
                "dependsOn": {"ghcr.io/x/b": {}, "ghcr.io/x/a:1": {"opt": "v"}},
                "installsAfter": ["ghcr.io/x/c"],
                "containerEnv": {"FOO": "bar"},
            }
        )
    )
    spec = read_feature_spec(tmp_path)
    assert spec.depends_on == ("ghcr.io/x/a:1", "ghcr.io/x/b:latest")
    assert spec.installs_after == ("ghcr.io/x/c:latest",)
    assert spec.container_env == {"FOO": "bar"}


def test_direct_depends_on_is_pulled_and_ordered_before_dependent(reg: FakeRegistry):
    reg.add(
        "ghcr.io/x/app:latest",
        id="app",
        depends_on={"ghcr.io/x/pixi:latest": {}},
    )
    reg.add("ghcr.io/x/pixi:latest", id="pixi")

    resolved = resolve_feature_graph({"ghcr.io/x/app:latest": {}}, reg.pull).unwrap()

    assert [r.id for r in resolved] == ["pixi", "app"]
    assert "ghcr.io/x/pixi:latest" in reg.pulled


def test_transitive_depends_on_chain_orders_deepest_first(reg: FakeRegistry):
    reg.add("ghcr.io/x/a:latest", id="a", depends_on={"ghcr.io/x/b:latest": {}})
    reg.add("ghcr.io/x/b:latest", id="b", depends_on={"ghcr.io/x/c:latest": {}})
    reg.add("ghcr.io/x/c:latest", id="c")

    resolved = resolve_feature_graph({"ghcr.io/x/a:latest": {}}, reg.pull).unwrap()

    assert [r.id for r in resolved] == ["c", "b", "a"]


def test_installs_after_orders_without_pulling_and_ignores_refs_not_in_set(
    reg: FakeRegistry,
):
    reg.add(
        "ghcr.io/x/a:latest",
        id="a",
        installs_after=[
            "ghcr.io/x/b:latest",
            "ghcr.io/devcontainers/features/common-utils",
        ],
    )
    reg.add("ghcr.io/x/b:latest", id="b")

    resolved = resolve_feature_graph(
        {"ghcr.io/x/a:latest": {}, "ghcr.io/x/b:latest": {}}, reg.pull
    ).unwrap()

    assert [r.id for r in resolved] == ["b", "a"]
    assert "ghcr.io/devcontainers/features/common-utils:latest" not in reg.pulled
    assert sorted(reg.pulled) == ["ghcr.io/x/a:latest", "ghcr.io/x/b:latest"]


def test_tagless_depends_on_is_normalised_and_dedupes_against_explicit_latest(
    reg: FakeRegistry,
):
    reg.add(
        "ghcr.io/x/app:latest",
        id="app",
        depends_on={"ghcr.io/x/pixi": {}},  # tagless
    )
    reg.add("ghcr.io/x/pixi:latest", id="pixi")

    resolved = resolve_feature_graph(
        {"ghcr.io/x/pixi:latest": {}, "ghcr.io/x/app:latest": {}}, reg.pull
    ).unwrap()

    assert [r.id for r in resolved] == ["pixi", "app"]
    assert len(resolved) == 2
    assert reg.pulled.count("ghcr.io/x/pixi:latest") == 1


def test_diamond_is_ordered_deterministically(reg: FakeRegistry):
    reg.add(
        "ghcr.io/x/a:latest",
        id="a",
        depends_on={"ghcr.io/x/b:latest": {}, "ghcr.io/x/c:latest": {}},
    )
    reg.add("ghcr.io/x/b:latest", id="b", depends_on={"ghcr.io/x/d:latest": {}})
    reg.add("ghcr.io/x/c:latest", id="c", depends_on={"ghcr.io/x/d:latest": {}})
    reg.add("ghcr.io/x/d:latest", id="d")

    resolved = resolve_feature_graph({"ghcr.io/x/a:latest": {}}, reg.pull).unwrap()

    ids = [r.id for r in resolved]
    assert ids.index("d") < ids.index("b")
    assert ids.index("d") < ids.index("c")
    assert ids.index("b") < ids.index("a")
    assert ids.index("c") < ids.index("a")
    # deterministic tie-break: b before c (dependsOn discovery order)
    assert ids == ["d", "b", "c", "a"]


def test_dependency_cycle_returns_err(reg: FakeRegistry):
    reg.add("ghcr.io/x/a:latest", id="a", depends_on={"ghcr.io/x/b:latest": {}})
    reg.add("ghcr.io/x/b:latest", id="b", depends_on={"ghcr.io/x/a:latest": {}})

    result = resolve_feature_graph({"ghcr.io/x/a:latest": {}}, reg.pull)

    assert result.is_err()
    assert "cycle" in str(result.unwrap_err())


def test_container_env_is_carried_onto_resolved_feature(reg: FakeRegistry):
    reg.add(
        "ghcr.io/x/a:latest",
        id="a",
        container_env={"FOO": "bar", "PATH": "/opt/a/bin:${PATH}"},
    )

    resolved = resolve_feature_graph({"ghcr.io/x/a:latest": {}}, reg.pull).unwrap()

    assert resolved[0].container_env == {"FOO": "bar", "PATH": "/opt/a/bin:${PATH}"}


def test_explicit_options_land_on_right_feature_and_depends_on_pulled_get_empty(
    reg: FakeRegistry,
):
    reg.add(
        "ghcr.io/x/app:latest",
        id="app",
        depends_on={"ghcr.io/x/pixi:latest": {}},
    )
    reg.add("ghcr.io/x/pixi:latest", id="pixi")

    resolved = resolve_feature_graph(
        {"ghcr.io/x/app:latest": {"version": "2", "extra": "yes"}}, reg.pull
    ).unwrap()

    by_id = {r.id: r for r in resolved}
    assert by_id["app"].options == {"version": "2", "extra": "yes"}
    assert by_id["pixi"].options == {}


def test_missing_depends_on_target_surfaces_pull_error(reg: FakeRegistry):
    reg.add(
        "ghcr.io/x/app:latest",
        id="app",
        depends_on={"ghcr.io/x/ghost:latest": {}},
    )

    result = resolve_feature_graph({"ghcr.io/x/app:latest": {}}, reg.pull)

    assert result.is_err()


def test_toolchain_depends_on_pixi_resolves_pixi_first(reg: FakeRegistry):
    """The Critical finding's motivating case: a Python toolchain feature on a
    slim base that dependsOn pixi must install pixi first."""
    reg.add(
        "ghcr.io/x/python-toolchain:latest",
        id="python-toolchain",
        depends_on={"ghcr.io/x/pixi:latest": {}},
    )
    reg.add("ghcr.io/x/pixi:latest", id="pixi")

    resolved = resolve_feature_graph(
        {"ghcr.io/x/python-toolchain:latest": {}}, reg.pull
    ).unwrap()

    assert [r.id for r in resolved] == ["pixi", "python-toolchain"]


def test_explicit_listing_order_is_the_primary_tie_break(reg: FakeRegistry):
    """Two independent explicit features keep their devcontainer.json order."""
    reg.add("ghcr.io/x/zeta:latest", id="zeta")
    reg.add("ghcr.io/x/alpha:latest", id="alpha")

    resolved = resolve_feature_graph(
        {"ghcr.io/x/zeta:latest": {}, "ghcr.io/x/alpha:latest": {}}, reg.pull
    ).unwrap()

    assert [r.id for r in resolved] == ["zeta", "alpha"]


def test_ref_to_id_strips_tag_digest_and_path():
    assert ref_to_id("ghcr.io/jesserobertson/devcontainers/pixi:latest") == "pixi"
    assert ref_to_id("ghcr.io/x/pixi@sha256:abc123") == "pixi"
    assert ref_to_id("ghcr.io/x/pixi") == "pixi"


def test_load_cached_specs_reads_every_subdir_keyed_by_id(tmp_path, monkeypatch):
    from devtemplate.config import Settings

    feats = tmp_path / "features"
    for name, dep in (("homebrew", None), ("pixi", None), ("py-devtools", "homebrew")):
        d = feats / f"hash-{name}"
        d.mkdir(parents=True)
        spec = {"id": name, "version": "1.0.0"}
        if dep:
            spec["dependsOn"] = {f"ghcr.io/jesserobertson/devcontainers/{dep}": {}}
        (d / "devcontainer-feature.json").write_text(json.dumps(spec))
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    specs = load_cached_specs(Settings())
    assert set(specs) == {"homebrew", "pixi", "py-devtools"}
    assert specs["py-devtools"].depends_on == (
        "ghcr.io/jesserobertson/devcontainers/homebrew:latest",
    )


def test_load_cached_specs_empty_when_dir_absent(tmp_path, monkeypatch):
    from devtemplate.config import Settings

    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    assert load_cached_specs(Settings()) == {}


def test_load_cached_specs_skips_malformed_dir(tmp_path, monkeypatch):
    from devtemplate.config import Settings

    feats = tmp_path / "features"
    (feats / "hash-ok").mkdir(parents=True)
    (feats / "hash-ok" / "devcontainer-feature.json").write_text('{"id": "ok"}')
    (feats / "hash-bad").mkdir(parents=True)
    (feats / "hash-bad" / "devcontainer-feature.json").write_text("{ not json")
    monkeypatch.setattr(Settings, "data_dir", property(lambda self: tmp_path))
    assert set(load_cached_specs(Settings())) == {"ok"}
