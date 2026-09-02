# Release process for this repo

This is a monorepo with three independently-released things: the `dvt` CLI,
the devcontainer Features (`features/*`), and the base images (`base/`).
Each has its own release mechanism - there's no single "cut a release for
the whole repo" step.

## dvt (the CLI, published to PyPI as `dvt-cli`)

1. **Bump `dvt/pyproject.toml`'s `[project] version`.** That's the only
   version string dvt has - no separate `__init__.py`/`pixi.toml` copy to
   keep in sync.
2. **Update `dvt/CHANGELOG.md`**: move `[Unreleased]` under a new dated
   `## [X.Y.Z] - YYYY-MM-DD` heading.
3. **Verify locally** (from `dvt/`):
   ```bash
   pixi run check-all   # tests + quality
   ```
4. **Commit and push to `main`.**
5. **Tag and push**:
   ```bash
   git tag -a dvt-vX.Y.Z -m "dvt X.Y.Z"
   git push origin dvt-vX.Y.Z
   ```
   This one tag triggers two independent workflows:
   - `release-dvt.yml` - creates a GitHub Release, body pulled straight
     from the matching `dvt/CHANGELOG.md` section.
   - `publish-dvt.yml` - builds the package and auto-publishes it to
     **TestPyPI** (cheap, reversible - no human checkpoint needed there).
6. **Sanity-check the TestPyPI upload**:
   ```bash
   pip install -i https://test.pypi.org/simple/ dvt-cli==X.Y.Z
   ```
7. **Publish to real PyPI manually**: GitHub → Actions → *Publish dvt to
   PyPI* → *Run workflow* → choose `pypi`. Never automatic - a published
   version on real PyPI can't be reused, so it stays behind a deliberate
   human trigger, same as logerr's own release process.

Trusted publishing (OIDC, no token) needs registering once per environment
at https://pypi.org/manage/account/publishing/ and the TestPyPI equivalent
- PyPI project name `dvt-cli`, Owner `jesserobertson`, Repo
  `devcontainers`, Workflow `publish-dvt.yml`, Environment `pypi` (and
  `testpypi`).

### CI, on every push/PR touching `dvt/**`

`dvt-ci.yml` runs quality checks and the full test matrix (ubuntu/macos/
windows), and uploads coverage + test results to Codecov, tagged by OS so
platform-only code (`pty/windows.py`, `pty/posix.py`, the `win32` branches
in `runtime.py`) doesn't read as permanently uncovered just because one
platform's leg didn't touch it. `CODECOV_TOKEN` is per-repo - if coverage
stops showing up, check that this repo's secret actually holds *this*
repo's Codecov token and not another repo's (the upload log's "results
will be available at: https://app.codecov.io/github/<owner>/<repo>/..."
line tells you which project a run actually landed under).

## Features (`features/*`, published to `ghcr.io`)

Features don't use a "cut a release" step at all - `publish-features.yml`
runs on every push to `main` that touches `features/**`, and
`devcontainers/action` only republishes a Feature whose
`devcontainer-feature.json` `version` actually changed since the last
publish. So the entire release step is:

1. Bump the `version` field in the Feature's `devcontainer-feature.json`.
2. Commit and push to `main` - it publishes itself.

Tagging is a separate, purely cosmetic step for a GitHub Release entry (not
required for the publish to happen):

```bash
git tag -a feat-<name>-vX.Y.Z -m "<name> feature X.Y.Z"
git push origin feat-<name>-vX.Y.Z
```

`release-features.yml` picks that up and creates a GitHub Release, body
pulled from the Feature's own `description` field.

## Base images (`base/Dockerfile`)

Unversioned - just `:latest` (and a CUDA-version-suffixed tag for
`base-cuda`). `build.yml` rebuilds and pushes on every push to `main` that
touches `base/Dockerfile`, `images/**`, or the `homebrew` / `shell-kit` /
`pixi` features. No tagging, no changelog entry; the top-level
`CHANGELOG.md` documents these with dated sections instead of version
numbers, matching the fact that nothing here ships as a single versioned
artifact (see its own header for why).

`base/Dockerfile` is `core` -> `slim` only, and the build runs in two phases:

1. **`docker build --target slim`** publishes `base-ubuntu-slim` (from
   `ubuntu:24.04`) and `base-cuda-slim` (from
   `nvidia/cuda:12.8.0-devel-ubuntu24.04`). Covered by `build.yml`'s `build-slim`
   job and the two `-slim` entries in `build-images.ps1`'s `$ImageDefs`.
2. **`devcontainer build --push`** assembles `base-ubuntu` and `base-cuda` from
   `<matching -slim> + the homebrew, shell-kit and pixi features`, per
   `images/base-ubuntu/.devcontainer/devcontainer.json` and
   `images/base-cuda/.devcontainer/devcontainer.json`. Covered by `build.yml`'s
   `build-bundles` job (`needs: build-slim`) and the `Builder = 'devcontainer'`
   entries in `$ImageDefs`.

The bundle configs pin the three plumbing features at `:latest`, so a bundle
build only picks up new `homebrew` / `shell-kit` / `pixi` content once
`publish-features.yml` has actually published it. When a single push changes both
a plumbing feature and something that triggers `build.yml`, the bundle built in
that run still sees the *previous* feature content - the bumped feature lands in
the *next* `build.yml` run (or a manual `workflow_dispatch`). Bump a plumbing
feature, let its publish finish, then rely on a bundle rebuild.
