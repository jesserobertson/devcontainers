"""Build-time devcontainer-Feature dependency resolution.

`dvt`'s image builder installs the Features a project's devcontainer.json lists.
On its own that ignores each Feature's *own* declared ordering metadata, which
breaks a project whose base image relies on a `dependsOn` Feature (e.g. a Python
toolchain on a slim base that `dependsOn`s `pixi`). This module closes that gap:
given the explicitly-listed Feature refs it pulls their `devcontainer-feature.json`,
follows every `dependsOn` transitively (pulling those too), and returns the full
set in a deterministic install order that respects `dependsOn` / `installsAfter`.

This is a pragmatic subset of the devcontainer install-order spec, not a faithful
implementation:

- No `overrideFeatureInstallOrder` (the consumer-side manual override list).
- No round-based scoring / soft-dependency heuristics - just a stable topological
  sort over the hard `dependsOn` + `installsAfter` edges.
- Only `dependsOn` triggers a pull. An `installsAfter` (or `dependsOn`) ref that
  resolves to something not in the resolved set is ignored for ordering; a
  `dependsOn` target the registry can't serve surfaces as the pull's own error.
- A `dependsOn` target's per-dependency option object (`{"<ref>": {<options>}}`)
  is NOT applied - a transitively-pulled feature always installs with its default
  options. A project needing non-default options for a dependency must list that
  ref explicitly in its own `features`.
- `containerEnv` values are passed through verbatim to a plain Docker `ENV`
  instruction. `${VAR}` therefore works with ordinary shell/Docker semantics;
  the spec's `${containerEnv:VAR}` self-referential interpolation is NOT
  implemented.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from logerr import Ok, Result
from logerr.utilities import wrap_result

from devtemplate.config import Settings

__all__ = [
    "FeatureSpec",
    "ResolvedFeature",
    "GraphNode",
    "read_feature_spec",
    "normalise_ref",
    "ref_to_id",
    "load_cached_specs",
    "resolve_feature_graph",
    "describe_graph",
    "to_dot",
    "to_mermaid",
]


@dataclass(frozen=True)
class FeatureSpec:
    """The ordering-relevant slice of a Feature's own devcontainer-feature.json.

    `depends_on` / `installs_after` hold refs already run through `normalise_ref`
    (a tagless ref becomes `:latest`), so they compare directly against the
    normalised refs `resolve_feature_graph` keys its working set by.
    """

    id: str
    depends_on: tuple[str, ...]
    installs_after: tuple[str, ...]
    container_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedFeature:
    """One Feature in the resolved install order: where its extracted files are,
    the options it should be installed with, and the containerEnv it contributes.
    """

    id: str
    ref: str
    extracted_dir: Path
    options: dict[str, str]
    container_env: dict[str, str]


@dataclass(frozen=True)
class GraphNode:
    """One feature's place in the dependency graph, in short-id terms.

    `pulls_in` is the transitive closure of this feature's `dependsOn` edges among
    the described spec set - each entry a short id (`ref_to_id`) when that id is
    itself in the set, otherwise the bare normalised ref. It is sorted and
    deduped, and excludes the feature itself. `installs_after` is this feature's
    own `installsAfter` list mapped the same way (id where known, bare ref
    otherwise) with its original order preserved; it is NOT transitive and never
    contributes to `pulls_in`.
    """

    id: str
    pulls_in: tuple[str, ...]
    installs_after: tuple[str, ...]


def normalise_ref(ref: str) -> str:
    """Append `:latest` when the ref's trailing path segment carries neither a
    `:tag` nor an `@sha256:` digest, so a tagless `dependsOn` entry dedupes
    against an explicitly-pinned `...:latest`.

    >>> normalise_ref("ghcr.io/devcontainers/features/pixi")
    'ghcr.io/devcontainers/features/pixi:latest'
    >>> normalise_ref("ghcr.io/x/pixi:1.2.3")
    'ghcr.io/x/pixi:1.2.3'
    """
    last_segment = ref.rsplit("/", 1)[-1]
    if ":" in last_segment or "@" in last_segment:
        return ref
    return f"{ref}:latest"


def ref_to_id(ref: str) -> str:
    """Short id from an OCI ref's trailing path segment:
    'ghcr.io/x/pixi:latest' -> 'pixi'. Strips an '@sha256:…' digest first, then
    a ':tag', then takes the last path segment."""
    body = ref.split("@", 1)[0]
    body = body.rsplit(":", 1)[0] if ":" in body.rsplit("/", 1)[-1] else body
    return body.rsplit("/", 1)[-1]


def _normalise_all(refs: Iterable[str]) -> tuple[str, ...]:
    return tuple(normalise_ref(str(ref)) for ref in refs)


def read_feature_spec(extracted_dir: Path) -> FeatureSpec:
    """Parse the ordering metadata out of `extracted_dir/devcontainer-feature.json`.

    `dependsOn`'s object form (`{"<ref>": {<options>}}`) is reduced to a sorted
    tuple of its normalised ref keys; a list form is accepted too. `installsAfter`
    is already a list of refs and keeps its order (normalised). `containerEnv`
    defaults to `{}`. Raises (KeyError / JSONDecodeError / OSError) on a missing
    or malformed file - `resolve_feature_graph` turns that into an `Err`.
    """
    data = json.loads((extracted_dir / "devcontainer-feature.json").read_text())

    raw_depends = data.get("dependsOn", {})
    if isinstance(raw_depends, dict):
        depends_on = tuple(sorted(_normalise_all(raw_depends.keys())))
    elif isinstance(raw_depends, list):
        depends_on = tuple(sorted(_normalise_all(raw_depends)))
    else:
        depends_on = ()

    raw_installs_after = data.get("installsAfter", [])
    installs_after = (
        _normalise_all(raw_installs_after)
        if isinstance(raw_installs_after, list)
        else ()
    )

    raw_env = data.get("containerEnv", {})
    container_env = (
        {str(k): str(v) for k, v in raw_env.items()}
        if isinstance(raw_env, dict)
        else {}
    )

    return FeatureSpec(
        id=str(data["id"]),
        depends_on=depends_on,
        installs_after=installs_after,
        container_env=container_env,
    )


def load_cached_specs(settings: Settings) -> dict[str, FeatureSpec]:
    """Every readable devcontainer-feature.json under settings.features_dir/*/,
    keyed by FeatureSpec.id. {} when the directory is absent. A subdir whose
    read_feature_spec raises is skipped."""
    directory = settings.features_dir
    if not directory.exists():
        return {}
    specs: dict[str, FeatureSpec] = {}
    for child in sorted(p for p in directory.iterdir() if p.is_dir()):
        try:
            spec = read_feature_spec(child)
        except (OSError, ValueError, KeyError):
            continue
        specs[spec.id] = spec
    return specs


def _find_cycle(
    nodes: list[str], after: dict[str, set[str]], specs: dict[str, FeatureSpec]
) -> str:
    """Return a human-readable `a -> b -> a` trace of one cycle among `nodes`."""
    nodeset = set(nodes)
    path: list[str] = []
    on_path: set[str] = set()

    def walk(node: str) -> list[str] | None:
        path.append(node)
        on_path.add(node)
        for nxt in sorted(after[node] & nodeset):
            if nxt in on_path:
                return [*path[path.index(nxt) :], nxt]
            found = walk(nxt)
            if found is not None:
                return found
        path.pop()
        on_path.discard(node)
        return None

    for start in nodes:
        found = walk(start)
        if found is not None:
            return " -> ".join(specs[ref].id for ref in found)
    return " -> ".join(specs[ref].id for ref in nodes)  # pragma: no cover


@wrap_result
def resolve_feature_graph(
    explicit: dict[str, dict[str, str]],
    pull: Callable[[str], Path],
) -> Result[list[ResolvedFeature], Exception]:
    """Resolve `explicit` (devcontainer.json's `features` map: ref -> options) into
    a fully-ordered `list[ResolvedFeature]`.

    `pull` is an already-unwrapped pull callback (raises on failure). It is
    called once per distinct normalised ref - the explicit ones plus every ref
    reached transitively through `dependsOn`. `installsAfter` never triggers a
    pull.

    Ordering is a stable topological sort over the edges "A installs after B"
    (B in `A.depends_on` or B in `A.installs_after`, and B is in the resolved
    set). Among the nodes currently free of unmet edges the next one is the
    minimum of this priority key:

      1. explicitly-listed refs before `dependsOn`-discovered refs;
      2. within each group, discovery order (explicit-listing order, or the
         order `dependsOn` traversal first reached the ref);
      3. feature id, alphabetically, as a final total-order tie-break.

    A cycle returns `Err(ValueError("feature dependency cycle: a -> b -> a"))`.
    """
    explicit_norm: dict[str, dict[str, str]] = {}
    explicit_order: list[str] = []
    for ref, options in explicit.items():
        normalised = normalise_ref(ref)
        if normalised not in explicit_norm:
            explicit_order.append(normalised)
        explicit_norm[normalised] = dict(options)

    specs: dict[str, FeatureSpec] = {}
    extracted: dict[str, Path] = {}
    dependson_index: dict[str, int] = {}
    dependson_counter = 0

    queue: list[str] = list(explicit_order)
    while queue:
        ref = queue.pop(0)
        if ref in specs:
            continue
        extracted_dir = pull(ref)
        spec = read_feature_spec(extracted_dir)
        specs[ref] = spec
        extracted[ref] = extracted_dir
        for dep in spec.depends_on:
            if dep in specs or dep in queue:
                continue
            queue.append(dep)
            if dep not in explicit_norm and dep not in dependson_index:
                dependson_index[dep] = dependson_counter
                dependson_counter += 1

    after: dict[str, set[str]] = {ref: set() for ref in specs}
    for ref, spec in specs.items():
        for other in (*spec.depends_on, *spec.installs_after):
            if other in specs and other != ref:
                after[ref].add(other)

    def priority(ref: str) -> tuple[int, int, str]:
        if ref in explicit_norm:
            return (0, explicit_order.index(ref), specs[ref].id)
        return (1, dependson_index[ref], specs[ref].id)

    ordered: list[str] = []
    done: set[str] = set()
    while len(ordered) < len(specs):
        ready = [ref for ref in specs if ref not in done and after[ref] <= done]
        if not ready:
            remaining = [ref for ref in specs if ref not in done]
            raise ValueError(
                f"feature dependency cycle: {_find_cycle(remaining, after, specs)}"
            )
        nxt = min(ready, key=priority)
        ordered.append(nxt)
        done.add(nxt)

    return Ok(
        [
            ResolvedFeature(
                id=specs[ref].id,
                ref=ref,
                extracted_dir=extracted[ref],
                options=explicit_norm.get(ref, {}),
                container_env=specs[ref].container_env,
            )
            for ref in ordered
        ]
    )


def _label(ref: str, ids: set[str]) -> str:
    """The short id for `ref` when that id is one of `ids`, else the bare ref."""
    ident = ref_to_id(ref)
    return ident if ident in ids else ref


@wrap_result
def describe_graph(
    specs: Mapping[str, FeatureSpec],
) -> Result[dict[str, GraphNode], Exception]:
    """One `GraphNode` per spec, keyed exactly as `specs` is.

    `pulls_in` is the transitive `dependsOn` closure among `specs` (labels via
    `_label`); `installs_after` is the feature's own `installsAfter` mapped the
    same way, order preserved, and never folded into `pulls_in`. A `dependsOn`
    cycle among the described features returns
    `Err(ValueError("feature dependency cycle: a -> b -> a"))`.
    """
    ids = set(specs)
    # id -> its dependsOn targets as labels (short id where known, bare ref else)
    edges: dict[str, list[str]] = {
        fid: [_label(dep, ids) for dep in spec.depends_on]
        for fid, spec in specs.items()
    }
    # dependsOn edges restricted to described nodes - the only ones that can cycle
    after: dict[str, set[str]] = {
        fid: {ident for dep in spec.depends_on if (ident := ref_to_id(dep)) in ids}
        for fid, spec in specs.items()
    }

    consumed: set[str] = set()
    while True:
        ready = [fid for fid in ids if fid not in consumed and after[fid] <= consumed]
        if not ready:
            break
        consumed.update(ready)
    remaining = sorted(ids - consumed)
    if remaining:
        trace = _find_cycle(remaining, after, dict(specs))
        raise ValueError(f"feature dependency cycle: {trace}")

    def closure(fid: str) -> set[str]:
        seen: set[str] = set()
        stack = list(edges[fid])
        while stack:
            dep = stack.pop()
            if dep in seen:
                continue
            seen.add(dep)
            if dep in ids:
                stack.extend(edges[dep])
        return seen

    return Ok(
        {
            fid: GraphNode(
                id=fid,
                pulls_in=tuple(sorted(closure(fid))),
                installs_after=tuple(_label(r, ids) for r in spec.installs_after),
            )
            for fid, spec in specs.items()
        }
    )


def _edges(nodes: Iterable[GraphNode]) -> list[tuple[str, str]]:
    """Sorted, deduped `(src, dst)` pairs - one per `src pulls in dst`."""
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        for dep in node.pulls_in:
            seen.add((node.id, dep))
    return sorted(seen)


def to_dot(nodes: Iterable[GraphNode]) -> str:
    """Graphviz DOT for the pull-in graph. `"A" -> "B"` means "A pulls in B"."""
    lines = ["digraph deps {"]
    lines += [f'  "{src}" -> "{dst}";' for src, dst in _edges(nodes)]
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(nodes: Iterable[GraphNode]) -> str:
    """Mermaid flowchart for the pull-in graph. `A --> B` means "A pulls in B"."""
    lines = ["graph TD"]
    lines += [f"  {src} --> {dst}" for src, dst in _edges(nodes)]
    return "\n".join(lines)
