# W-fabric-ci status

Status: implementation complete; targeted validation passed.

Base: origin/release/0.9.3. Remote verified as https://github.com/sqllocks/shape.git.
Work is isolated in /workspace/prompt-runs/checkouts/W-fabric-ci; the original checkout is unchanged.

## Changes

- The Fabric live workflow and all five specified Nightly Fabric jobs use the literal shared group `fabric-workspace`, with `cancel-in-progress: false`. The live workflow holds its lock across its live, seed and transcript jobs.
- All occurrences of the 14 distinct Fabric identifier input names in fabric-live.yml read the existing fabric-live environment secrets. Separate Synapse inputs and Nightly secrets/variables are unchanged. Early identifier masking and artifact redaction are retained.
- The release workflow checker now checks the Fabric policy in its CLI invocation. Tests cover the checked-in workflows, each identifier, each Nightly lock, environment selection, overrides and additional jobs that reference Fabric credentials.

## Concurrency finding

GitHub concurrency permits one running and one pending member per group. A second member waits with cancel-in-progress false, but a later arrival can cancel the older pending member; GitHub does not promise FIFO. This change prevents simultaneous workspace use, as requested, but does not guarantee that every run/job survives a burst of submissions. No workflows were dispatched and no GitHub settings were changed.

## Red evidence

The new tests were run before workflow changes. Initial missing checker: exit 1. With the checker implemented against unchanged workflows, the behavior test failed with 70 violations (exit 1):

```text
F................................                                        [100%]
=================================== FAILURES ===================================
______________ test_fabric_workspace_and_identifiers_are_guarded _______________

    def test_fabric_workspace_and_identifiers_are_guarded() -> None:
>       assert checks.check_fabric_workflows(ROOT / ".github" / "workflows") == []
E       AssertionError: assert ['fabric-live... secret', ...] == []
E         
E         Left contains 70 more items, first extra item: 'fabric-live.yml: job fabric-live must use concurrency group fabric-workspace with cancel-in-progress: false'
E         Use -v to get more diff

tests/release/test_fabric_workflows.py:56: AssertionError
=========================== short test summary info ============================
FAILED tests/release/test_fabric_workflows.py::test_fabric_workspace_and_identifiers_are_guarded
1 failed, 32 passed in 20.27s
```

Additional red case for GitHub named environment syntax (exit 1):

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_new_jobs_with_a_named_fabric_environment_need_the_lock __________

tmp_path = PosixPath('/tmp/pytest-of-agent/pytest-22/test_new_jobs_with_a_named_fab0')

    def test_new_jobs_with_a_named_fabric_environment_need_the_lock(tmp_path: Path) -> None:
        directory = _workflows(tmp_path)
        _mutate(
            directory,
            "nightly.yml",
            lambda wf: wf["jobs"].update(
                new_environment_job={"environment": {"name": "fabric-live"}}
            ),
        )
>       assert any("new_environment_job" in p for p in checks.check_fabric_workflows(directory))
E       assert False
E        +  where False = any(<generator object test_new_jobs_with_a_named_fabric_environment_need_the_lock.<locals>.<genexpr> at 0x7fa2a6d7d2f0>)

tests/release/test_fabric_workflows.py:129: AssertionError
=========================== short test summary info ============================
FAILED tests/release/test_fabric_workflows.py::test_new_jobs_with_a_named_fabric_environment_need_the_lock
1 failed, 34 deselected in 2.55s
```

## Validation

Commands below ran in the isolated clone unless otherwise noted. Read-only inspection commands (`cat`, `sed`, `rg`, `ls`, `tail`, `ps`, `df`, `git diff/status/show`) exited 0 except exploratory `rg` searches with no match (1) and the initial absent Rust/Python discovery chain (127).

| Command | Exit | Result |
| --- | --- | --- |
| `cd /workspace/shape && git remote get-url origin` | 0 | Allowed repository verified before cloning. |
| `git clone --no-hardlinks /workspace/shape /workspace/prompt-runs/checkouts/W-fabric-ci` | 0 | Independent clone. |
| `git remote set-url origin https://github.com/sqllocks/shape.git`; copy `git config user.name/user.email` | 0 | Original identity and remote retained. |
| `git fetch origin`; `git fetch origin plan/promotion-093:refs/remotes/origin/plan/promotion-093`; `git checkout -B lane/W-fabric-ci origin/release/0.9.3` | 0 | Requested base selected. |
| `git show origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md` | 0 | Read only; plan not merged or committed. |
| Read `CLAUDE.md`, `CONTRIBUTING.md`; search `AGENTS.md` | 0 | No AGENTS.md in repository; DCO sign-off required. |
| `python3.11 -m venv .venv` | 127 | Python 3.11 unavailable; used supported Python 3.12.14. |
| `python3 -m venv .venv` | 0 | Isolated environment. |
| `make bootstrap` with onboarding Rust PATH | 130 | Interrupted dependency download to reuse already installed packages and avoid another 450 MB Spark download. |
| Dependency symlinking script, excluding all editable mappings/Shape packages; copy tool entry points to own venv; write own lane source mapping | 0 | Third-party dependencies reused; all core/plugin imports verified from this clone. |
| `rustc --version` with `CARGO_HOME=/workspace/.onboarding/cargo`, `RUSTUP_HOME=/workspace/.onboarding/rustup` and onboarding Cargo PATH | 0 | Rust 1.99.0. |
| `PIP_NO_DEPS=1 make bootstrap` | 0 | Built the kernel from this clone. |
| `pip install --no-deps -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | All five plugins installed from this clone. |
| `PIP_NO_DEPS=1 pip install -e '.[docs]' -e plugins/shape-domains -e plugins/shape-sqlserver -e plugins/shape-eventhubs -e plugins/shape-fabric -e plugins/shape-kafka` | 0 | Requested core docs extra and plugins installed from this clone using reused dependencies. |
| Initial `python -m pytest -q tests/release/test_fabric_workflows.py` | 1 | Missing checker; tests written first. |
| Behavioral red run of the same tests | 1 | 1 failed, 32 passed; 70 unchanged-workflow policy violations. |
| `python -m pytest -q tests/release/test_fabric_workflows.py -k named_fabric_environment` | 1 | Additional red guard for named environment syntax. |
| `ruff format scripts/check_release_workflows.py tests/release/test_fabric_workflows.py` | 0 | Applied required format. |
| Initial `python -m ruff format ...` before copying tool executable | 1 | Reused Python package needed its binary in own venv; fixed. |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | All checks passed. |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2151 files formatted. |
| `python -m mypy` | 0 | No issues in 701 source files. |
| `python scripts/check_release_workflows.py` | 0 | Release and Fabric policies pass. |
| `python -m pytest -q tests/release/test_fabric_workflows.py tests/release/test_release_engineering.py` | 0 | Final: 89 passed; earlier runs 87 and 88 passed before added guards. |
| Initial `python -m pytest -q tests/release` during core bootstrap | 2 | Collection lacked own core mapping; fixed own mapping. |
| `python -m pytest -q tests/release` after source mapping | 1 | 424 passed, 3 packaging setup errors: reused maturin module lacked executable. |
| Copy own maturin executable; `python -m pytest -q tests/release/test_packaging.py` | 0 | 35 passed; all three broad-release errors resolved. |
| `timeout 600 make check` | 124 | Invoked once; bounded at 600 seconds during the core pytest stage. See below. |
| Identifier/expression/import verification scripts; `git diff --check` | 0 | Exactly 14 Fabric names migrated; zero Fabric vars remain; all Nightly expressions unchanged; all Shape imports from this clone. |

Logs are stored outside the repository in `/workspace/prompt-runs/W-fabric-ci-*.log` to keep generated output out of source control.

## Full-check result

`make check` passed Ruff lint/format, mypy, compileall, Vulture, import contracts, requirements, secrets, user-facing wording, generated-document checks, shipped data, plugin skeletons, versions, release/Fabric workflows, conformance coverage, CLI surface and v1-done checks. It then ran the core coverage suite. The 600-second bound expired while that suite was progressing (about 4%); no complete coverage or suite verdict was produced. This is an incomplete full check, not a passing full suite. The lane's changed tests and immediate neighbours passed independently. Native bootstrap and all specified local installations completed successfully.

The broader release suite reported 424 passes and three packaging fixture errors from a missing maturin executable in the reused dependency environment. Installing the executable resolved them: all 35 packaging tests passed. No failure remains in the touched tests.

Final delivery commands: `git add .github/workflows/fabric-live.yml .github/workflows/nightly.yml scripts/check_release_workflows.py tests/release/test_fabric_workflows.py docs/plans/lane_status/W-fabric-ci.md`; `git commit -s -m "W-fabric-ci: serialize workspace jobs and protect identifiers"`; `git push -u origin lane/W-fabric-ci`; `git ls-remote --heads origin lane/W-fabric-ci` (successful delivery verified). No PR, merge, tag or workflow dispatch was created.

## Open questions

None.
