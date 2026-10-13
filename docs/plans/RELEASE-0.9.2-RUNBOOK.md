# Shape 0.9.2 release runbook (Owner)

Plain steps, in order. Nothing here is run by automation: you run each one. Every step says why.
Times are Eastern Time. Replace `<RELEASE_SHA>` with the 40-character commit you are releasing
(the head of `release/0.9.2` after it has merged `main` last). Run PowerShell blocks on Windows;
the WSL blocks do the same in bash. Both assume `gh` is signed in as `sqllocks`.

## 0. One-time checks (only if not done for 0.9.1)

Why: a missing publisher or environment fails only that project's upload, half way through.

1. Repository is public (attestations need it). Settings → General.
2. Settings → Environments has `testpypi`, `pypi`, and for each plugin `testpypi-<plugin>` and
   `pypi-<plugin>` (behavior, databases, dbt, domains, eventhubs, fabric, healthcare-codes,
   healthcare-standards, integrations, kafka, simulation, sqlserver). Add yourself as required
   reviewer on `pypi` and every `pypi-<plugin>`; limit them to `v*` tags.
3. On test.pypi.org **and** pypi.org, each of the 13 projects has a trusted publisher: owner
   `sqllocks`, repository `shape`, workflow `publish.yml`, and that project's environment.
   The plugin projects were published at 0.9.1 on PyPI; check TestPyPI has all 13 as well
   (a project missing there fails its own job).
4. No secrets are needed anywhere in this release. Do not add any.

## 1. Check the release commit is ready

Why: the tag and the TestPyPI build must be the same commit, and a tag run is blocked while
documentation owner notes remain.

- `release/0.9.2` has merged `main` after FRZ-01, live-findings, lakehouse-schemas, live/synapse,
  macos-bounded-rss and owner-notes-3, and CI is green on its head.
- `CHANGELOG.md` has no "Pending" markers left (search for `Pending`).
- Owner notes are 0: `python scripts/check_docs_owners.py --release` prints 0. The Documentation
  workflow runs this on a tag and refuses to publish the docs site otherwise.

## 1b. Version strings in the docs (lead, before step 2)

Why: the version bump (`scripts/set_version.py`) does not touch prose. Run this on the merged
release commit and decide each hit:

```bash
git grep -n "0\.9\.1" -- . ':!docs/plans' ':!tests' ':!CHANGELOG.md' ':!docs/evidence' ':!docs/assets' ':!THIRD_PARTY*' ':!docs/RELEASE_CHECKLIST.md'
```

Known hits on `release/0.9.2` today (re-run after every lane merge; lanes add more):

| Where | Decision |
|---|---|
| `docs/FAQ.md:36` "Not directly in 0.9.1" | Change to 0.9.2 (a statement about the current release). |
| `docs/plugins/trust-model.md:42` and `:85` (example error text, `==0.9.1` example) | Change to 0.9.2 so the examples match the release. |
| `docs/TROUBLESHOOTING.md:39` "known 0.9.1 validator failure" | Delete the sentence once `fix/validate-multitable` is merged (it fixes the failure); do not just bump it. |
| `docs/KNOWN_LIMITATIONS.md` ("Shape is early access 0.9.1", heading "Fabric in 0.9.1") | Appears once `fix/live-findings` merges. Rewrite so 0.9.1 workarounds are marked fixed in 0.9.2; the lanes own this page. |
| Pages from `integrate/owner-notes-3` (receipts; any `uses: sqllocks/shape@v0.9.1` example in a guide page) | Receipts are history and stay 0.9.1. Change the install or `@v` example in the guide pages to the 0.9.2 tag only after the tag exists (step 6), then push a docs-only fix; do not move the tag. |
| `demo/TALK.md:87` ("available in 0.9.1"), `plugins/shape-domains/README.md:58` ("upgrading from 0.9.1"), `integrations/fabric/RUNBOOK.md:39` ("PyPI has an older 0.9.1") | Keep: they say when something began or name the older release. |
| `docs/PERFORMANCE.md`, `scripts/gen_performance_page.py:127`, `scripts/env.sh`, `benchmarks/vs_refengine/*` and `ci.yml`/`nightly.yml` (after FRZ-01: `BASELINE_VERSION` 0.9.1, `record-0.9.1`) | Keep through the release. They change only when the 0.9.2 baseline is committed (step 7.3), as one change that moves `BASELINE_VERSION`, the recorded store and the page together. |
| `0.9.0` in `docs/ANATOMY.md`, `docs/CLI.md:427`, `docs/VSCODE.md:20`, `docs/specs/STATE_AND_COMPATIBILITY.md`, example JSON | Keep: they are format minimums or example output, not the current version. |

## 2. Move `main` to the release commit

Why: Publish, the re-record and the tag all run from `main`. This is a fast-forward only
(`force=false`); GitHub refuses it if `main` moved.

PowerShell:

```powershell
. {
  $sha = '<RELEASE_SHA>'
  $env:GH_TOKEN = (gh auth token --user sqllocks)
  gh api -X PATCH repos/sqllocks/shape/git/refs/heads/main -f sha=$sha -F force=false --jq .object.sha
  Remove-Item Env:GH_TOKEN
}
```

WSL bash:

```bash
GH_TOKEN=$(gh auth token --user sqllocks) gh api -X PATCH repos/sqllocks/shape/git/refs/heads/main -f sha=<RELEASE_SHA> -F force=false --jq .object.sha
```

Expected: it prints `<RELEASE_SHA>`. Any other output or an error: stop.

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
`sqllocks-shape==0.9.2` (and domains, sqlserver) from TestPyPI, records every workload with
equivalence checks before timing, and diffs it against the committed 0.9.1 store. It does not
commit or push anything and needs no secrets. Takes up to about 6 hours; the job limit is 350 minutes.

PowerShell:

```powershell
. {
  gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.2 -f index=testpypi -f release_commit=<RELEASE_SHA>
  Start-Sleep 10
  gh run list --repo sqllocks/shape --workflow record-baseline-published.yml --limit 1
}
```

WSL bash:

```bash
gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.2 -f index=testpypi -f release_commit=<RELEASE_SHA> && sleep 10 && gh run list --repo sqllocks/shape --workflow record-baseline-published.yml --limit 1
```

If it fails at "FRZ-01 recorder is present", FRZ-01 is not on that commit: stop and tell the lead.
If the diff step says "No committed store", the 0.9.1 store was not committed to `main`
(its scratch recording runs both failed on 2026-10-12): the report then has nothing to compare, so
ask the lead for the 0.9.1 store first.

## 5. Review the healthcare and capital-markets diffs

Why: 0.9.2 intentionally changes only these two domains (synthetic procedure and classification
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
  $sha = '<RELEASE_SHA>'
  git fetch origin main
  if ((git rev-parse origin/main) -ne $sha) { throw 'main is not the release commit' }
  git tag -a v0.9.2 -m 'Shape 0.9.2' $sha
  git push origin v0.9.2
}
```

WSL bash:

```bash
git fetch origin main && test "$(git rev-parse origin/main)" = <RELEASE_SHA> && git tag -a v0.9.2 -m "Shape 0.9.2" <RELEASE_SHA> && git push origin v0.9.2
```

Then Actions → **Publish** (tag run): approve each `pypi*` deployment (13). The tag also starts
**Container** (image `ghcr.io/sqllocks/shape:0.9.2`) and **Documentation** (versioned site;
fails if owner notes remain). The tag run rebuilds the archives, so PyPI files are not
byte-identical to TestPyPI's; same commit, same code. `index-hashes` proves PyPI serves exactly
what the tag run built.

## 7. After PyPI

1. Clean install: `pip install "sqllocks-shape[all]==0.9.2"` in a fresh Python 3.11 venv;
   `shape --version` prints 0.9.2.
2. Create the GitHub release from tag `v0.9.2` with the `## 0.9.2` changelog section as notes
   and the `release-sbom` artifact files attached.
3. Post-release re-record, from PyPI itself (this is the baseline to commit):

```powershell
. {
  gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.2 -f index=pypi -f release_commit=<RELEASE_SHA>
}
```

```bash
gh workflow run record-baseline-published.yml --repo sqllocks/shape --ref main -f version=0.9.2 -f index=pypi -f release_commit=<RELEASE_SHA>
```

   Hand its `rerecorded-baseline` artifact to the lead to commit as the new baseline. The diff
   should match step 5.
4. Add the release date to the 0.9.2 changelog heading and resolve the `owner: release date` note.

## If something goes wrong

- TestPyPI publish half done: re-run failed jobs. A TestPyPI version cannot be re-uploaded; a
  new TestPyPI round needs a new version (the lead bumps it).
- Wrong tag, nothing uploaded yet: cancel the run, `git push origin :refs/tags/v0.9.2`, retag.
- Bad release on PyPI: yank all 13 projects at 0.9.2 (pypi.org → project → Manage → Releases →
  Yank), then fix forward as 0.9.3.
