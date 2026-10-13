# Shape 0.9.3 release runbook (Owner)

Plain steps, in order. Nothing here is run by automation: you run each one. Every step says why.
Times are Eastern Time. Replace `<RELEASE_SHA_093>` with the 40-character commit you are releasing
(the head of `release/0.9.3` after it has merged `main` last). Run PowerShell blocks on Windows;
the WSL blocks do the same in bash. Both assume `gh` is signed in as `sqllocks`.

**0.9.2 exists only on TestPyPI.** It was uploaded from `c10923be`, can never be replaced there, and
was never published to PyPI. 0.9.3 carries everything 0.9.2 had. Nothing is uploaded, no tag is
created, and `main` does not move until step 0 is fully green and the Owner says go.

SHAs for this release (fill in as they exist; a placeholder stays until it is real):

| Name | SHA |
|---|---|
| Frozen base `int/0.9.2` | `e0e1cf50b64f43649165485f3706bab52081f8a0` |
| `int/0.9.2` after the fixes merged | `<INT_092_FIXED_SHA>` |
| `release/0.9.3` head = `<RELEASE_SHA_093>` | `<RELEASE_SHA_093>` |
| `main` before step 2 | `<MAIN_BEFORE_SHA>` |
| TestPyPI build run (step 3) | `<TESTPYPI_RUN_ID>` |
| Tag `v0.9.3` (step 6) | `<TAG_SHA_093>` (must equal `<RELEASE_SHA_093>`) |

## 0. GO / NO-GO checklist (all green before step 2 and step 3)

Why: 0.9.2 reached TestPyPI before it was ready. Run every command against `<RELEASE_SHA_093>`
and write the result in the Result column. One red or blank row is NO-GO. Rows are checked on
the exact release commit; if `release/0.9.3` moves, redo them all. Replace `<SHA>` with
`<RELEASE_SHA_093>`; `R=sqllocks/shape`.

| # | Check | Command | Expected | Result |
|---|---|---|---|---|
| 1 | CI green on the release commit | `gh run list -R $R --workflow ci.yml --commit <SHA> --json conclusion,status,databaseId` | a `completed` run with `"conclusion":"success"` | |
| 2 | Dry-run publish green on the release commit | `gh workflow run publish.yml -R $R --ref release/0.9.3 -f repository=dry-run`, then `gh run list -R $R --workflow publish.yml --commit <SHA> --json conclusion,databaseId,event` | `workflow_dispatch` run `success`; its Summary lists the four skipped upload/index jobs as "dry run"; no deployment was requested | |
| 3 | fabric-live green on the release commit | `gh workflow run fabric-live.yml -R $R --ref release/0.9.3`, then `gh run list -R $R --workflow fabric-live.yml --commit <SHA> --json conclusion,databaseId` | `success`; every skipped live test in the Summary is named with a reason you accept | |
| 4 | Scheduled nightlies green on the release commit, or rerun | `gh run list -R $R --commit <SHA> --event schedule --json name,conclusion`; for each of `nightly.yml`, `fabric-live.yml`, `docs-links.yml` without a green run on `<SHA>`: `gh workflow run <file> -R $R --ref release/0.9.3` | each of the three has a `success` run on `<SHA>` | |
| 5 | Owner notes 0 | `python scripts/check_docs_owners.py --release` | last line `Documentation owners: 0 unresolved`, exit 0 | |
| 6 | Versions agree and the tree is releasable | `python scripts/check_versions.py --release` | exit 0; `python scripts/check_versions.py --list` shows 0.9.3 in every location | |
| 7 | CHANGELOG clean | `grep -n "Pending" CHANGELOG.md; sed -n '/^## Unreleased/,/^## 0.9.3/p' CHANGELOG.md` | grep prints nothing; Unreleased reads "No unreleased changes."; `## 0.9.3` carries the line "0.9.2 was published to TestPyPI only and was never released to PyPI; its changes are part of 0.9.3." | |
| 8 | Every open bug is fixed or a stated boundary | `gh api "repos/sqllocks/shape/issues?state=open&per_page=100" --jq '.[] \| select(.pull_request\|not) \| "#\(.number) [\([.labels[].name]\|join(","))] \(.title)"'` | list every issue labelled `bug` or with a bug-like title; each is either fixed in 0.9.3 (CHANGELOG entry) or a documented boundary in `docs/KNOWN_LIMITATIONS.md`. Write the list below. | |
| 9 | KNOWN_LIMITATIONS reviewed | `git log --oneline -3 -- docs/KNOWN_LIMITATIONS.md` and read it | no 0.9.1/0.9.2 workaround left unmarked; reads 0.9.3; reviewer initials and date in Result | |
| 10 | No unexplained failing scheduled run on main in 24 hours | `gh run list -R $R --branch main --event schedule --created ">=$(date -u -d '24 hours ago' +%FT%TZ)" --json name,conclusion,databaseId,url` | no `failure`/`cancelled`, or each has a written explanation here | |
| 11 | The Owner says go | the Owner writes "go" for `<RELEASE_SHA_093>` | explicit "go" in this row, with date and time | |

Open bug issues (row 8). Snapshot taken 13 Oct from `main`, **re-run at go time**; none of the 18 open
issues carries a label. Disposition is the Owner's call:

| Issue | Why it is on the list | Disposition |
|---|---|---|
| #1 shape profile fails on any nested column (struct, list, map); Delta table aborts the process | bug by title | `<fixed in 0.9.3: CHANGELOG line> / <KNOWN_LIMITATIONS section>` |
| #19 REL-091: fix the 14 failing CI and Nightly jobs on main | release tracker, not a product bug | `<close or note>` |
| #2-#17, #20 (W9-01 to W9-16, FRZ-01) | feature work and a comparison task, not bugs | not bugs; not listed |

Do not start step 2 or step 3 while any row is blank or red.

## 0b. One-time checks (only if not done for 0.9.1 or 0.9.2)

Why: a missing publisher or environment fails only that project's upload, half way through.

1. Repository is public (attestations need it). Settings → General.
2. Settings → Environments has `testpypi`, `pypi`, and for each plugin `testpypi-<plugin>` and
   `pypi-<plugin>` (behavior, databases, dbt, domains, eventhubs, fabric, healthcare-codes,
   healthcare-standards, integrations, kafka, simulation, sqlserver). Add yourself as required
   reviewer on `pypi` and every `pypi-<plugin>`; limit them to `v*` tags.
3. On test.pypi.org **and** pypi.org, each of the 13 projects has a trusted publisher: owner
   `sqllocks`, repository `shape`, workflow `publish.yml`, and that project's environment.
   The plugin projects were published at 0.9.1 on PyPI. 0.9.2 was uploaded to TestPyPI from c10923be, so
   all 13 projects already exist there; 0.9.3 is a new version on both indexes.
4. No secrets are needed anywhere in this release. Do not add any.

## 1. Check the release commit is ready

Why: the tag and the TestPyPI build must be the same commit, and a tag run is blocked while
documentation owner notes remain.

- The release commit is the head of `release/0.9.3` (created from the frozen `int/0.9.2` e0e1cf50b64f43649165485f3706bab52081f8a0,
  plus the merged fixes and the 0.9.2 → 0.9.3 bump). `<RELEASE_SHA_093>` in the steps below is that SHA.
  If `release/0.9.3` moves, every item of step 0 starts again on the new head.
- `CHANGELOG.md` has no "Pending" markers left (search for `Pending`) and `## Unreleased` holds
  no entries (it reads "No unreleased changes.").
- Owner notes are 0: `python scripts/check_docs_owners.py --release` prints 0. The Documentation
  workflow runs this on a tag and refuses to publish the docs site otherwise.

## 1b. Version strings in the docs (lead, before step 2)

Why: the version bump (`scripts/set_version.py`) does not touch prose. Run this on the merged
release commit and decide each hit:

```bash
git grep -n "0\.9\.2" -- . ':!docs/plans' ':!tests' ':!CHANGELOG.md' ':!docs/evidence' ':!docs/assets' ':!THIRD_PARTY*' ':!docs/RELEASE_CHECKLIST.md'
```

Decide each hit. Release-facing prose (banners, install examples, "Early access 0.9.2") moves to 0.9.3.
History stays as written: evidence files, receipts and run records that name a 0.9.2 run, and any
sentence saying when something began or that names 0.9.2 as the older release (0.9.2 exists only on
TestPyPI, so say "0.9.2 (TestPyPI only)" rather than "the previous release"). Fill the table below
on the release commit; the 0.9.2 runbook's decisions are history.

| Where | Decision |
|---|---|
| (filled in on the release commit) | |

The baseline files (`BASELINE_VERSION`, `record-0.9.1`, the recorded store and `docs/PERFORMANCE.md`)
stay at 0.9.1: 0.9.2 was never on PyPI, so no 0.9.2 baseline exists. They change only when the 0.9.3
baseline is committed (step 7.3), as one change.

## 2. Move `main` to the release commit

Why: Publish, the re-record and the tag all run from `main`. This is a fast-forward only
(`force=false`); GitHub refuses it if `main` moved.

PowerShell:

```powershell
. {
  $sha = '<RELEASE_SHA_093>'
  $env:GH_TOKEN = (gh auth token --user sqllocks)
  gh api -X PATCH repos/sqllocks/shape/git/refs/heads/main -f sha=$sha -F force=false --jq .object.sha
  Remove-Item Env:GH_TOKEN
}
```

WSL bash:

```bash
GH_TOKEN=$(gh auth token --user sqllocks) gh api -X PATCH repos/sqllocks/shape/git/refs/heads/main -f sha=<RELEASE_SHA_093> -F force=false --jq .object.sha
```

Expected: it prints `<RELEASE_SHA_093>`. Any other output or an error: stop.

## 3. Publish to TestPyPI

Why: a rehearsal on the real index; PyPI uploads can never be replaced.

PowerShell:

```powershell
. {
  gh workflow run publish.yml --repo sqllocks/shape --ref main -f repository=testpypi
  Start-Sleep 10
  gh run list --repo sqllocks/shape --workflow publish.yml --limit 1
}
```

WSL bash:

```bash
gh workflow run publish.yml --repo sqllocks/shape --ref main -f repository=testpypi && sleep 10 && gh run list --repo sqllocks/shape --workflow publish.yml --limit 1
```

Then on github.com → Actions → that run: approve each `testpypi*` deployment when asked
(13 projects). All jobs must be green: `build`, `publish`, `index-hashes`, `installed`,
`installed-musllinux`. If `publish` fails part way, **re-run failed jobs** (never start a new
run for the same version). This run is the build you are releasing; note its run number.

## 4. Re-record the regression baseline from the TestPyPI build

Why: the baseline must be the release users get, not the source tree. This installs
`sqllocks-shape==0.9.3` (and domains, sqlserver) from TestPyPI, records every workload with
equivalence checks before timing, and diffs it against the committed 0.9.1 store. It does not
commit or push anything and needs no secrets. Takes up to about 6 hours; the job limit is 350 minutes.

PowerShell:

```powershell
. {
  gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.3 -f index=testpypi -f release_commit=<RELEASE_SHA_093>
  Start-Sleep 10
  gh run list --repo sqllocks/shape --workflow record-baseline-published.yml --limit 1
}
```

WSL bash:

```bash
gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.3 -f index=testpypi -f release_commit=<RELEASE_SHA_093> && sleep 10 && gh run list --repo sqllocks/shape --workflow record-baseline-published.yml --limit 1
```

If it fails at "FRZ-01 recorder is present", FRZ-01 is not on that commit: stop and tell the lead.
If the diff step says "No committed store", the 0.9.1 store was not committed to `main`
(its scratch recording runs both failed on 2026-10-12): the report then has nothing to compare, so
ask the lead for the 0.9.1 store first.

## 5. Review the healthcare and capital-markets diffs

Why: since 0.9.1 the release intentionally changes only these two domains (synthetic procedure and classification
labels, so new dataset ids). Any other domain changing, or any row-count or column change, is a bug.

- Phone: open the run on github.com; the diff is in the run **Summary** page.
- Full files: download the artifact.

PowerShell:

```powershell
. {
  $id = '<RECORD_RUN_ID>'   # the id shown by gh run list
  gh run download $id --repo sqllocks/shape -n rerecorded-baseline -D rerecorded
  Get-Content rerecorded\baseline-diff.md
}
```

WSL bash:

```bash
gh run download <RECORD_RUN_ID> --repo sqllocks/shape -n rerecorded-baseline -D rerecorded && cat rerecorded/baseline-diff.md
```

Accept when: healthcare and capital_markets show CHANGED with the same `rows` and `columns`
and different `dataset_id`/`parquet_sha256`; every other domain says "unchanged". Otherwise stop
and do not run step 6.

## 6. Publish to PyPI by pushing the tag

Why: PyPI publishes only from a `v*` tag (guard job). The tag must name the same commit TestPyPI
built. This **cannot be undone**: a PyPI file name is used forever. Yank and fix forward if wrong.

PowerShell:

```powershell
. {
  $sha = '<RELEASE_SHA_093>'
  git fetch origin main
  if ((git rev-parse origin/main) -ne $sha) { throw 'main is not the release commit' }
  git tag -a v0.9.3 -m 'Shape 0.9.3' $sha
  git push origin v0.9.3
}
```

WSL bash:

```bash
git fetch origin main && test "$(git rev-parse origin/main)" = <RELEASE_SHA_093> && git tag -a v0.9.3 -m "Shape 0.9.3" <RELEASE_SHA_093> && git push origin v0.9.3
```

Then Actions → **Publish** (tag run): approve each `pypi*` deployment (13). The tag also starts
**Container** (image `ghcr.io/sqllocks/shape:0.9.3`) and **Documentation** (versioned site;
fails if owner notes remain). The tag run rebuilds the archives, so PyPI files are not
byte-identical to TestPyPI's; same commit, same code. `index-hashes` proves PyPI serves exactly
what the tag run built.

## 7. After PyPI

1. Clean install: `pip install "sqllocks-shape[all]==0.9.3"` in a fresh Python 3.11 venv;
   `shape --version` prints 0.9.3.
2. Create the GitHub release from tag `v0.9.3` with the `## 0.9.3` changelog section as notes
   and the `release-sbom` artifact files attached.
3. Post-release re-record, from PyPI itself (this is the baseline to commit):

```powershell
. {
  gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.3 -f index=pypi -f release_commit=<RELEASE_SHA_093>
}
```

```bash
gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.3 -f index=pypi -f release_commit=<RELEASE_SHA_093>
```

   Hand its `rerecorded-baseline` artifact to the lead to commit as the new baseline. The diff
   should match step 5.
4. Add the release date to the 0.9.3 changelog heading and resolve the `owner: release date` note.

## If something goes wrong

- TestPyPI publish half done: re-run failed jobs. A TestPyPI version cannot be re-uploaded; a
  new TestPyPI round needs a new version (the lead bumps it).
- Wrong tag, nothing uploaded yet: cancel the run, `git push origin :refs/tags/v0.9.3`, retag.
- Bad release on PyPI: yank all 13 projects at 0.9.3 (pypi.org → project → Manage → Releases →
  Yank), then fix forward as 0.9.4.
