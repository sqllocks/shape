# W-fabric-ci review

Reviewer: independent (did not build this lane). Head reviewed: `f3bae98431ec993772e899f5b52a6662a803a4e9`
(matches the expected prefix), diff `origin/release/0.9.3...HEAD` (base `a8e476ca`).

## Verdict: REJECT

Part (2) is correct: all 14 identifiers in every `fabric-live.yml` job (`fabric-live`, `seed`,
`docs-fabric-transcripts`, including the `AZURE_*` and `SHAPE_TEST_SYNAPSE_*_ID` aliases) now
read `secrets.*`; the only `vars.` left are the six Synapse names, which are outside the 14; the
`::add-mask::` steps are unchanged and still first; nothing echoes an ID or secret; `nightly.yml`
repository-level secrets are untouched. No workflow was dispatched.

Part (1) serializes the workspace but, as written, will cancel Fabric jobs every night. See
finding 1.

## Commands

| Command | Exit | Result |
| --- | --- | --- |
| `ruff check src tests plugins benchmarks/vs_refengine scripts` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine scripts` | 0 | 2151 files formatted |
| `mypy` | 0 | no issues in 701 files |
| `python scripts/check_release_workflows.py` | 0 | OK (now also runs the Fabric checks) |
| `python -m pytest -p no:cov -q tests/release` | 0 | 429 passed (includes `test_fabric_workflows.py`) |
| `python -c yaml.safe_load(...)` on `fabric-live.yml`, `nightly.yml` | 0 / 0 | parse |
| `actionlint 1.7.7 -shellcheck=` on `fabric-live.yml`, `nightly.yml` | 0 / 0 | no findings (base also 0) |
| Job inventory of both workflows (YAML walk of every job, its `needs`, `if`, environment, concurrency and Fabric/ONELAKE/ABFSS/EventHubs/Synapse references) | 0 | the workspace jobs are exactly the five named nightly jobs plus the three `fabric-live.yml` jobs; all eight are in `fabric-workspace` with `cancel-in-progress: false` (`fabric-live.yml` via its workflow-level group) |
| Checker mutation probes (scratch copy of `.github/workflows`) | - | see findings 2-4 |

Checker mutation probes: `vars.FABRIC_KQL_DB` in a step `run` DETECTED; `vars['FABRIC_WORKSPACE_ID']`
DETECTED; workflow-level `concurrency` removed DETECTED; `vars.FABRIC_WORKSPACE_ID` in the
workflow-level `env:` MISSED; a job-level `fabric-workspace` group inside `fabric-live.yml`
MISSED; a new nightly job reading only `secrets.SHAPE_LIVE_ABFSS_URI` without the lock MISSED.

## Findings

1. **Blocking**, `.github/workflows/nightly.yml:219,267,304,382,411`: the five Fabric jobs have
   no `needs:` between them, so on every scheduled run all five are queued at the same moment
   into one concurrency group. GitHub keeps at most one running and one pending member per group,
   and a newly queued member cancels the existing pending one. Result: one job runs, one waits,
   and three are cancelled every night (the run ends `cancelled`, and which Fabric checks run is
   arbitrary). The lane status file mentions the one-pending rule but calls it a burst-only risk;
   it is the normal case here. Fix, then extend the checker for it: either chain the five jobs
   with `needs:` and `if: ${{ !cancelled() }}` (so they run one after another inside the run and
   still run when an earlier one fails), or, only if confirmed in current GitHub docs, add the
   `queue: max` concurrency option to every member. A test should fail when two Fabric jobs in
   `nightly.yml` can be queued at the same time.
2. Non-blocking, `scripts/check_release_workflows.py:265`: the `vars.` check only reads job
   bodies, so `vars.FABRIC_*` in the workflow-level `env:` of `fabric-live.yml` passes. Include
   the top-level `env` in the scan.
3. Non-blocking, `scripts/check_release_workflows.py:277`: a job in `fabric-live.yml` that also
   declares the job-level `fabric-workspace` group (same group as its own workflow) is accepted,
   although it would wait on its own run (GitHub reports a concurrency deadlock). Reject a
   job-level group equal to the workflow-level one.
4. Non-blocking, `scripts/check_release_workflows.py:270`: auto-detection of new workspace jobs
   keys on `FABRIC_` names only; a new nightly job that reaches the workspace through other
   names (for example the OneLake `SHAPE_LIVE_ABFSS_URI` that `abfss-live` uses) is not caught.
   The explicit job list covers today's jobs; consider adding the known non-`FABRIC_` names.
5. Non-blocking, `.github/workflows/nightly.yml:423-428`: `fabric-git-sync-live` still reads the
   IDs from the `fabric-live` environment's variables (outside this lane's scope, as the task
   limited (2) to `fabric-live.yml`). If those variables are ever removed now that secrets exist,
   `HAVE_GIT_SYNC` turns false and the job silently reports "nothing to run". Worth a follow-up.
