# Shape 0.9.4 release runbook (Owner)

Plain steps, in order. The Owner runs every step below. Release preparation only merges
one reviewed PR and rehearses Release build; it never tags or publishes.
The Owner approved 0.9.4 from main at 035cac28 or later on 2026-11-07, with an explicit
exception to the standing no-version-bumps rule for the preparation PR only.
All timestamps below are UTC. Commands use bash and an authenticated `gh` as `sqllocks`.

| Name | Value |
|---|---|
| Preparation PR | https://github.com/sqllocks/shape/pull/60 |
| Approved base | `035cac28491a62eff4b747f0b73f9629266992a2` |
| Base CI | https://github.com/sqllocks/shape/actions/runs/42395913048 |
| Base Nightly (28 green jobs) | https://github.com/sqllocks/shape/actions/runs/42395928925 |
| Release commit | `<RELEASE_SHA_094>`: preparation PR merge SHA, 40 hex characters |
| Release build rehearsal | <REHEARSAL_RUN_URL> |
| TestPyPI Publish run | `<TESTPYPI_RUN_ID>` |
| Tag | `v0.9.4`, resolving to `<RELEASE_SHA_094>` |

## 0. GO / NO-GO checklist

One red or blank row is NO-GO. Record results and links on the exact release commit.
Base CI and Nightly above establish the approved starting point, not proof for a later SHA.
In a clean checkout, set `R=sqllocks/shape` and replace every angle-bracket placeholder.

| Check | Exact action | Expected / result to record |
|---|---|---|
| Preparation PR | Open https://github.com/sqllocks/shape/pull/60 → Checks | Merged, every required check green; record merge SHA and CI links |
| CI | `gh run list -R "$R" --workflow ci.yml --commit <RELEASE_SHA_094> --json conclusion,status,url` | completed / success on release commit |
| Rehearsal | Actions → Release build → open <REHEARSAL_RUN_URL> | success on release commit, release=true; version 0.9.4, every job and Fabric wheel-size check green |
| Nightly | `gh run list -R "$R" --workflow nightly.yml --commit <RELEASE_SHA_094> --json conclusion,status,url` | success on exact SHA; if absent, Actions → Nightly → Run workflow → main, then verify head SHA |
| Versions and changelog | `python scripts/check_versions.py --release --expect 0.9.4 --list` | every location 0.9.4, final version, 0.9.4 section exists; Unreleased empty |
| Documentation owners | `python scripts/check_docs_owners.py --release` | 0 unresolved; resolve any blocking notes before publishing |
| Public issues | `gh issue list -R "$R" --state open --limit 100` | none, or explicit Owner disposition for each new issue |
| Limitations | Read `docs/KNOWN_LIMITATIONS.md` and 0.9.4 changelog | BigQuery experimental; Snowflake/Databricks live read validation pending Owner accounts; accept boundaries |
| Owner GO | Record date, SHA and explicit GO in preparation PR | authorization for this exact commit to reach TestPyPI, then review again before PyPI |

## 0b. One-time checks

1. GitHub → repository Settings → General: public repository (needed for attestations).
2. Settings → Environments: `testpypi`, `pypi` and each plugin's matching environments exist.
   Require Owner review on `pypi*` and allow `v*` tags.
3. On test.pypi.org and pypi.org → each project → Publishing: trusted publisher is Owner
   `sqllocks`, repository `shape`, workflow `publish.yml`, matching environment. Check all 13:
   core plus behavior, databases, dbt, domains, eventhubs, fabric, healthcare-codes,
   healthcare-standards, integrations, kafka, simulation and sqlserver.
4. No publishing tokens or passwords are needed. Do not add secrets.

## 1. Check the release commit is ready

Use the preparation PR's **merge SHA**, not its branch head. It includes current main.

```bash
R=sqllocks/shape
git fetch origin main
git show <RELEASE_SHA_094>:pyproject.toml
python scripts/check_versions.py --release --expect 0.9.4
```

Run the Python check from a checkout of that SHA. `## Unreleased` is empty; `## 0.9.4`
holds Added, Changed, Fixed, Privacy and Development. Its heading deliberately has no date,
matching 0.9.3; the Owner supplies the release date after PyPI (step 7).

## 1b. Version strings in the docs

```bash
git grep -n '0\.9\.3' -- ':!docs/plans' ':!tests' ':!CHANGELOG.md' ':!docs/evidence' ':!docs/assets' ':!THIRD_PARTY*'
```

| Where | Decision |
|---|---|
| Current Early access banners, docs site banner and limitation status | 0.9.4 |
| Current ADF image defaults and Fabric library installation guidance | 0.9.4 |
| Existing runbooks | Unchanged per preparation scope; replace 0.9.3 with 0.9.4 when following their installation examples |
| Recorded CLI/bridge/manifest examples, Playground session and historical fix claims | Keep 0.9.3: recorded output and history |
| Third-party notices, evidence, tests, baseline stores/version and performance records | Keep historical values; baseline changes only after step 7.3 |

## 2. Confirm main contains the release commit

The preparation PR is already merged through the PR merge API. Do not directly rewrite main.

```bash
git fetch origin main
test "$(git rev-parse origin/main)" = <RELEASE_SHA_094>
gh pr view 60 -R "$R" --json state,mergeCommit,url
```

Expected: equality succeeds and PR state is MERGED. If main has moved, stop and decide whether
to release the reviewed SHA using its immutable ref or prepare and recheck the newer tree.
The commands below assume main still names the release commit; verify before each dispatch.

## 3. Publish to TestPyPI

GitHub → Actions → **Publish** → **Run workflow** → branch **main** → repository
**testpypi** → **Run workflow**. Equivalent command:

```bash
gh workflow run publish.yml -R "$R" --ref main -f repository=testpypi
gh run list -R "$R" --workflow publish.yml --commit <RELEASE_SHA_094> --json databaseId,status,conclusion,url
```

Open that run, verify its SHA and version 0.9.4, approve all requested `testpypi*`
deployments (13 projects). Wait for build, all publishes, index-hashes and all installed
smokes including Alpine to succeed. Record `<TESTPYPI_RUN_ID>`. For a partial publish use
run → **Re-run jobs** → **Re-run failed jobs**; do not start another build of this version.

## 4. Re-record the baseline from TestPyPI

Actions → **Re-record baseline from a published build** → **Run workflow** → main;
version **0.9.4**, index **testpypi**, release_commit **<RELEASE_SHA_094>**. Equivalent:

```bash
gh workflow run record-baseline-published.yml -R "$R" --ref main -f version=0.9.4 -f index=testpypi -f release_commit=<RELEASE_SHA_094>
gh run list -R "$R" --workflow record-baseline-published.yml --json databaseId,status,conclusion,url
```

This installs the published build, verifies equivalence before timing and uploads a diff;
it commits or pushes nothing. Allow up to 350 minutes. A missing recorder or committed store
is NO-GO: resolve it before accepting a diff.

## 5. Review the diffs

Open the recording run → **Summary**, then **Artifacts** → **rerecorded-baseline**.

```bash
gh run download <RECORD_RUN_ID> -R "$R" -n rerecorded-baseline -D rerecorded-testpypi
cat rerecorded-testpypi/baseline-diff.md
```

Review every workload against the committed published baseline. This release includes
broader generation and type changes than 0.9.3: do not reuse its two-domain-only acceptance
rule. Explain each changed dataset ID/hash, row/column count, error or timing result against
the 0.9.4 changelog and intended behavior. Unexplained differences or equivalence failures
are NO-GO. Record the Owner's acceptance and run link in the PR before step 6.

## 6. Push v0.9.4 to publish to PyPI

Owner only. PyPI filenames cannot be replaced. Confirm the SHA matches the successful
TestPyPI build and accepted recording, then:

```bash
git fetch origin main
test "$(git rev-parse origin/main)" = <RELEASE_SHA_094>
git tag -a v0.9.4 -m 'Shape 0.9.4' <RELEASE_SHA_094>
git push origin v0.9.4
```

Actions → **Publish** → the `v0.9.4` run → approve all 13 `pypi*` deployments. All build,
publish, index-hashes and installed jobs must succeed. The tag also triggers Container
(`ghcr.io/sqllocks/shape:0.9.4`) and Documentation; inspect both. The tag rebuilds archives,
so byte identity with TestPyPI is not expected; index-hashes verifies the actual PyPI files.

## 7. After PyPI

1. Clean Python 3.11 install outside the development environment:

```bash
python3.11 -m venv /tmp/shape-094-install
/tmp/shape-094-install/bin/python -m pip install 'sqllocks-shape[all]==0.9.4'
/tmp/shape-094-install/bin/shape --version
```

   Expected version: 0.9.4. Inspect the tag Publish install matrix on Python 3.11 and 3.14.
2. Actions → successful tag Publish run → Artifacts → download **release-sbom**.
   Repository → Releases → **Draft a new release** → choose existing **v0.9.4** → title
   **Shape 0.9.4** → paste the 0.9.4 changelog section → attach release-sbom files →
   **Publish release**. Owner only; do not create another tag.
3. Post-release recording from PyPI:

```bash
gh workflow run record-baseline-published.yml -R "$R" --ref main -f version=0.9.4 -f index=pypi -f release_commit=<RELEASE_SHA_094>
```

   Download **rerecorded-baseline**, review against step 5, and hand the artifact to the
   maintainer for a signed-off baseline PR updating the store, baseline version and associated
   performance records together. Do not commit the TestPyPI recording as the final baseline.
4. Add the actual release date to `## 0.9.4 - YYYY-MM-DD` in CHANGELOG.md through a signed-off
   PR, resolving any `owner: release date` note. The undated heading is the same Owner
   placeholder used by 0.9.3; no release date is invented during preparation.

## If something goes wrong

- Partial TestPyPI upload: re-run failed jobs of the original run; never overwrite files.
- Failure before any upload: stop and diagnose before retrying or changing a tag.
- Bad PyPI release: Owner yanks 0.9.4 on all 13 projects (project → Manage → Releases →
  Yank), then fixes forward as 0.9.5. Never reuse the released version.
