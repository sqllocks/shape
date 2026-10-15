# W-uv-1 — blocked before implementation

Date: 2026-10-15. Target: 0.9.4 development work; published versions remain 0.9.3.
Base: `origin/main` at `a8e476ca910c40e3d8e7dd0dace9c80699237e82`.
Branch: `lane/W-uv-1`. Origin verified as `https://github.com/sqllocks/shape.git`.

## Result and exact question

A universal uv workspace containing core and all 12 plugins cannot resolve the existing
published dependency constraints. Core requires `numpy>=2.0,<3`. The integrations plugin
publishes `anonymeter>=1,<2` in its `anonymeter` extra. Available anonymeter versions 1.0.0
and 1.1.0 require `numpy>=1.22,<1.27`. Those ranges have no intersection.
uv resolves workspace members' optional dependencies even when an extra is not selected
for installation. Not selecting the extra in `uv sync` therefore does not solve this.

**Question for the owner/lead:** Should W-uv-1 wait for a separately authorized correction
to the integrations plugin's published `anonymeter` extra (or a compatible upstream release),
or may this lane use a specifically approved exception to the complete-workspace lock
requirement? Which exception is authorized, and what compatibility evidence is required?

The task explicitly says: “If something in scope cannot move to uv without changing
user-facing behaviour or a published artefact, STOP on that item and write the exact
question in docs/plans/lane_status/W-uv-1.md (commit and push it) instead of guessing.”
No dependency override, removal of an extra, workspace exclusion, changed Python support,
or published metadata change has been assumed. This is a blocker report, not a completed
migration. Dependent Makefile, CI and contributor-instruction changes are deferred because
they would otherwise direct contributors to a lock that cannot be produced.

## Files changed

- `docs/plans/lane_status/W-uv-1.md` only.

No CHANGELOG migration claim is added because the migration has not happened.

## Tooling and proposed workspace

The initial `uv --version` was `uv 0.12.23 (x86_64-unknown-linux-gnu)`. Installed the requested
version with `python3 -m pip install --user "uv==0.8.17"` (exit 0), then selected it using
`export PATH="$HOME/.local/bin:$PATH"`; `uv --version` prints `uv 0.8.17` (exit 0).
This is the version mandated by the task and aligned with Premium. This tooling install
was outside the development environment. Python used by uv was CPython 3.12.14.
`rustc --version` returned 127 (not installed); no build validation is claimed.

Resolution was probed outside the repository, at
`/workspace/scratch/W-uv-1-resolution`. It copied all 13 pyproject files, retained all
`[project]` tables and build backends, and added only uv configuration: root project core,
`[tool.uv.workspace] members = ["plugins/*"]`, `required-version = "==0.8.17"`, and workspace
source mappings for local first-party dependencies. Each plugin needs a source mapping
for core; the root also maps every plugin. The first probe additionally copied the published
dev extra's requirements to a `dev` group and all non-comment requirements-docs lines to a
`docs` group. Removing both proposed groups did not eliminate the conflict.

A Python assertion comparing parsed `[project]` tables across origin/main, the checkout,
and the final probe passed for all 13 distributions (exit 0). This checks the probe's inputs;
it is **not** a substitute for the requested built wheel/sdist metadata test.

## Red evidence and command results

Commands ran from `/workspace/shape` unless shown otherwise. Read-only inspection commands
(`cat`, `rg`, `ls`, `sed`, `head`) read the requested source, configuration and workflow files;
no repository AGENTS.md exists on this base. `CLAUDE.md` points at CONTRIBUTING.md.
The combined initial tooling/read command ended at missing `rustc` with exit 127.
A later `ls -la ~/.cargo/bin` returned 2 (directory absent); `df -h /workspace` returned 0
(30 GiB available). `rg -n 'requirements-docs' --glob '!docs/plans/**'` returned 0 and
identified `scripts/docs_example_setup.py` as a consumer outside the converted workflows.
`test ! -f .github/dependabot.yml` returned 0.

| Command / check | Exit | Result |
| --- | ---: | --- |
| `git remote get-url origin` and suffix guard | 0 | Correct repository |
| `git status --short` before branching | 0 | Clean |
| `git fetch origin` | 0 | Updated origin/main |
| `git checkout -B lane/W-uv-1 origin/main` | 0 | Correct lane/base |
| Initial `uv --version` | 0 | 0.12.23; replaced for this task |
| `python3 -m pip install --user "uv==0.8.17"` | 0 | Installed mandated version |
| `uv --version` after PATH selection | 0 | 0.8.17 |
| Initial probe `uv lock --directory /workspace/scratch/W-uv-1-resolution` | 1 | Missing core workspace source mapping; corrected in probe |
| Probe `uv lock --directory /workspace/scratch/W-uv-1-resolution` with source mappings and groups | 1 | NumPy/anonymeter conflict below |
| Same `uv lock` after removing both proposed groups | 1 | Same conflict |
| Two-package `uv pip compile` below | 1 | Confirms incompatibility without workspace/groups |
| Python assertion of all 13 `[project]` tables | 0 | Identical probe and baseline inputs |
| `uv lock --check --directory /workspace/scratch/W-uv-1-resolution` | 2 | No lockfile can be checked |
| `uv sync --locked --all-groups --directory /workspace/scratch/W-uv-1-resolution` | 2 | No lockfile; created only a scratch venv |

Workspace resolver output (exit 1, both with and without proposed groups):

```text
No solution found when resolving dependencies for split (markers:
python_full_version >= '3.15' and sys_platform == 'win32'):
Because anonymeter>=1.0.0 depends on numpy>=1.22,<1.27 and only the
following versions of anonymeter are available:
    anonymeter<=1.0.0
    anonymeter==1.1.0
we can conclude that anonymeter>=1.0.0 depends on numpy>=1.22,<1.27.
And because sqllocks-shape-integrations[anonymeter]
depends on anonymeter>=1 and sqllocks-shape depends on
numpy>=2.0, we can conclude that sqllocks-shape[advanced] and
sqllocks-shape-integrations[anonymeter] are incompatible.
And because your workspace requires sqllocks-shape[advanced] and
sqllocks-shape-integrations[anonymeter], we can conclude that your
workspace's requirements are unsatisfiable.
```

The split shown by uv does not make this merely a Python 3.15 issue: the dependency ranges
are disjoint regardless of platform/Python. A reduced reproduction was also executed:

```sh
export PATH="$HOME/.local/bin:$PATH"
printf '%s\n' 'numpy>=2.0,<3' 'anonymeter>=1,<2' > /workspace/scratch/W-uv-1-incompatible.in
uv pip compile --universal --python-version 3.11 /workspace/scratch/W-uv-1-incompatible.in
```

Exit 1:

```text
warning: The requested Python version 3.11 is not available; 3.12.14 will be used to build dependencies instead.
No solution found when resolving dependencies:
Because only the following versions of anonymeter are available:
    anonymeter<=1.0.0
    anonymeter==1.1.0
and anonymeter>=1.0.0 depends on numpy>=1.22,<1.27, we can conclude that
anonymeter>=1.0.0 depends on numpy>=1.22,<1.27.
And because you require anonymeter>=1 and numpy>=2.0,<3, we can conclude
that your requirements are unsatisfiable.
```

No new behavior was implemented, so no behavioral tests were added, skipped or xfailed.
The red evidence above is a prerequisite resolver failure, not a test-first implementation
claim. The metadata-unchanged build test, clean-clone sync, `make check`, fresh-venv pip
fallback and actionlint were not run: there is no implementation to validate. Rust kernel
build, Windows/macOS marker verification and platform behavior remain unverified. No CI
matrix or gate changed. No known sandbox failure has been claimed without reproducing it.

## Timing

Cold origin/main bootstrap, cold lane bootstrap and warm uv sync timings were not measured.
There is no resolvable lane environment to compare; reporting a timing comparison would
misrepresent the blocked state.

## Workflows and platform notes

Converted workflows: none. The requested ci.yml, nightly.yml, docs.yml, docs-links.yml,
docs-transcripts.yml and security.yml remain on their original installs pending the lock
blocker. The min-versions floor test, wheel/user install tests and offline-lock outputs
remain unchanged. All OS/Python matrices remain unchanged; Windows path quoting and
macOS dependency differences have not yet been exercised through uv.

Deliberately out of scope and untouched: release.yml, wheels.yml, container.yml,
action-selftest.yml, record-baseline-published.yml, fabric-live.yml, Dockerfile, action.yml,
and all publish/sign/upload steps (including publish.yml and docs publishing).

## Dependabot proposal (not installed)

There is no existing `.github/dependabot.yml`. Per the task, do not add one; the proposed
configuration for owner review once a workspace lock exists is:

```yaml
version: 2
updates:
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: weekly
    groups:
      minor-and-patch:
        update-types:
          - minor
          - patch
```

## Completion checks

`git diff --check` returned 0 before staging the report.

Pending the owner/lead decision above. Only this blocker report is committed and pushed;
no PR, tag, force push, main push or other branch push is requested or performed.
