# W0-available status

Blocked before setup or implementation by the instruction to stop on ambiguity.

## Exact question

Which immutable baseline must `scripts/check_available.py` compare
`tests/docs/available_legacy.txt` against to enforce that the list may only
shrink, including when an addition is already committed? Section 1.3 names
only the mutable list. Should the check use a pinned initial inventory, a
specified Git ref/commit, or another approved baseline? Please specify the
baseline and the behavior when it is unavailable (including fixture trees
and shallow CI checkouts).

Without a baseline, adding a newly available page to the list would exempt
it from P-BAR, violating the shrink-only requirement. Comparing only with
HEAD would catch uncommitted additions but accept them after commit.

## Lead answer recorded: P-BAR 5

- This release is the version read from `pyproject.toml` at check time; do
  not hard-code the task's target release.
- Use that version's `## <version>` CHANGELOG section. If it does not
  exist, use `## Unreleased`.
- Rule 5 applies only to page row sets with `since:` equal to that version.
  Another version is not checked by rule 5.
- Tests must cover the matching version with a Promoted line in the numbered
  section; the same line in Unreleased when the numbered section is absent;
  a missing line failing P-BAR 5; and another since version being exempt.

## Work and command results

All repository commands below ran from `/workspace/shape`. Origin was
confirmed as `https://github.com/sqllocks/shape.git`. The working tree was
clean before branch setup. No plan branch was merged or committed.

1. `git remote get-url origin && git status --short && git fetch origin`:
   exit 0. No network retry needed.
2. `git checkout -B lane/W0-available origin/lane/W0-available`:
   exit 128; the default fetch refspec fetched only main, so the local
   remote-tracking lane ref was absent. Subsequent commands in this invocation
   did not run.
3. `git config --get-all remote.origin.fetch && git ls-remote --heads origin
   lane/W0-available && git fetch origin
   lane/W0-available:refs/remotes/origin/lane/W0-available`: exit 0.
   Verified the requested remote head `3515d1f4bbf797b69e3d6df9114a4be4c7a20052`.
   No network retry needed.
4. `git checkout -B lane/W0-available origin/lane/W0-available && git merge
   origin/main && cat CLAUDE.md && cat CONTRIBUTING.md && git fetch origin
   plan/promotion-093:refs/remotes/origin/plan/promotion-093 && git show
   origin/plan/promotion-093:docs/plans/PROMOTION_PLAN_0.9.3.md >
   /tmp/PROMOTION_PLAN.md`: exit 0. Merge reported already up to date.
   No merge commit was necessary. No network retry needed.
5. Read the plan opening, Decisions and complete section 1.3 using
   `sed -n '1,240p' /tmp/PROMOTION_PLAN.md`; read the previous lane status,
   `scripts/check_docs_owners.py` and `scripts/check_v1_done.py` with `cat`:
   exit 0. No repository AGENTS.md was present (workspace discovery exit 0).
6. `rg -n 'ratchet|shrink.only|legacy.txt|git show.*HEAD|merge-base' scripts
   tests/release Makefile`: exit 1 (no existing convention found).
   Subsequent commands in this invocation did not run.
7. `cat src/shape/cli/stability.py && sed -n '1,65p' docs/V1_DONE.md &&
   rg -n 'W0|ratchet|since:' /tmp/PROMOTION_PLAN.md && git log -3
   --format='%h %s' && git config user.name && git config user.email`:
   exit 0. Confirmed the plan supplies no comparison baseline.
8. Python replacement of this status file: exit 0.

Before repository work, the uploaded request was downloaded and read,
workspace instructions were discovered, and the cloud runtime skill and
network policy were read. Those setup/discovery actions changed no repository
files. The first upload download attempt failed while the environment was
starting; it succeeded after the environment became ready.

## Checks

- Baseline `make check`: not run; ambiguity requires stopping before setup.
- Red tests: not written or run.
- Final `make check`: not run.
- No implementation, release version, dependency files, regression store,
  or golden corpus was changed.
