# dvt Integration Tests + Full Docs Reference Design

**Date:** 2026-07-25
**Status:** Approved

## Overview

Two independent follow-ups to `dvt`'s now-complete implementation, both surfaced from an
explicit ask ("what's next?") and grounded in a real end-to-end smoke test performed before
this spec was written (pipx tooling itself hit an unrelated Windows bug; verified via a plain
`pip install` into a fresh venv instead, then ran the full lifecycle — `template sync`,
`project init`, `project add-feature`, and a real `devpod up`/`stop`/`delete` against Docker
Desktop). That smoke test found and fixed two real bugs already (`devpod` resolution via
`shutil.which()` on Windows, loguru sink noise) — this spec's job is the two things
identified as still worth doing: an automated integration-test tier, and full docs content.

## Part 1: Integration tests

**Location:** `dvt/tests/integration/test_devpod_lifecycle.py`, marked
`@pytest.mark.integration` (marker already registered in `pyproject.toml`), with
`pytest.mark.skipif(shutil.which("devpod") is None, reason="devpod not installed")` so it
skips cleanly (not fails) on machines without `devpod` — this tier is opt-in, run via
`pixi run test integration`, and deliberately NOT part of `pixi run test all` / the CI `test`
job (already decided: real containers in CI would be slow and add Docker-availability
flakiness to every push).

**Scope:**
- Uses a minimal, self-contained fixture `devcontainer.json` (a plain public base image —
  e.g. `mcr.microsoft.com/devcontainers/base:ubuntu` — no custom features, no
  `postCreateCommand`), written directly by the test, not fetched from GitHub. This
  deliberately isolates what the test verifies (does `dvt` correctly drive a real `devpod`
  through a container lifecycle) from the *separate*, already-flagged bug in this repo's own
  `features/*/install.sh` scripts (the "copy a default `pixi.toml` if none exists" logic
  runs at image-build time, before the real project directory is bind-mounted at
  `/workspace` at container start, so the default gets shadowed and `postCreateCommand: pixi
  install` fails). That bug belongs to the other, already-completed CLI-first-templates plan
  — this test must not be coupled to or blocked by it.
- Covers `dvt up` → `dvt stop` → `dvt delete`, each invoked for real (no mocking) via Typer's
  `CliRunner`, asserting real exit codes.
- Does **not** cover `dvt ssh`. During today's smoke test, `devpod ssh` failed with a
  permission error that reproduced identically calling bare `devpod ssh` directly — no `dvt`
  code involved at all — confirming it isn't something a `dvt`-level test could meaningfully
  assert on (most likely an artifact of a sandboxed shell's CWD handling, not a real-world
  issue). `ssh`'s command construction is already covered by existing unit tests (mocked
  `subprocess.run`), which is the correct layer for that concern.
- Teardown always runs via a pytest fixture (`try`/`finally`), regardless of test
  pass/fail/error, so a failed run never leaves an orphaned container behind.
- Does **not** touch `devpod`'s provider configuration (e.g. switching from `podman-windows`
  to `docker`, which today's smoke test needed once, manually) — that's host-level state a
  test must not mutate. If the developer's default provider isn't functional, the test fails
  with `devpod`'s own real error, which is a reasonable precondition for an opt-in,
  local-only integration tier.
- Workspace naming: derive a name that's unlikely to collide with a real workspace the
  developer already has (e.g. a `dvt-integration-test-<short-uuid>` pattern), so re-running
  the test doesn't require manual cleanup from a previous interrupted run.

## Part 2: Full docs reference

Replaces the current minimal `index.md`/`api.md` skeleton's thinness with real content,
added under `dvt/docs/content/`:

- `installation.md` — `pipx install ./dvt` (noting the network-access-to-a-git-dependency
  requirement already documented in the README), the Python ≥3.12 requirement, and the
  `devpod`/Docker prerequisite specifically for the lifecycle commands (`up`/`ssh`/`stop`/
  `delete` — `template`/`project` commands don't need either).
- `quickstart.md` — a real walkthrough: `dvt template sync` → `dvt template list` → `dvt
  project init --template <name> <path>` → `dvt project add-feature <name>` → `dvt up` →
  `dvt ssh`, using an actual template name from this repo (e.g. `fastapi`) throughout so the
  walkthrough is copy-pasteable.
- `commands.md` — full reference for every command: `template list`/`show`/`sync`, `project
  init`/`add-feature`, `up`/`ssh`/`stop`/`delete`, covering flags/arguments and exit-code
  behavior on failure (the refuse-and-leave-unchanged guarantees `add-feature`/`init` make).
- `concepts.md` — explains *why* `add-feature` behaves the way it does: the field-typed merge
  algorithm (scalar override, array dedup vs. concat, feature-key union, lifecycle-command
  union-if-both-object-form), `IDENTITY_FIELDS` stripping and why (a feature template's own
  `name` field is just its own identifier, not something that should overwrite the project's
  real name), and schema validation on write.
- `mkdocs.yml`'s `nav` updated to include all four new pages alongside the existing
  `index.md`/`api.md`.

## Out of scope

- The outer devcontainers repo's `features/*/install.sh` bind-mount-timing bug — flagged to
  the project owner, not fixed as part of this spec.
- Wiring the integration tier into CI, or adding `devpod ssh` coverage — both explicitly
  decided against above.
- GitHub Pages deployment for the docs site (`scripts/docs.py` still has no `deploy`
  command) — not asked for here.
