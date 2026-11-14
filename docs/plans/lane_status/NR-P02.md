# NR-P02 — Fabric platform inventory refresh

Status: independent source review approved; ready for CI.
Review date: **2026-11-14**. Branch: `lane/NR-P02`; base: `12bdfd5`.

## Scope and source review

Refreshed all 25 inventory rows. All 28 distinct cited Learn pages returned HTTP 200
on **2026-11-14**. Dates below are retrieval dates, not page publication dates.
No runtime, guard script, fixture, workflow or shared integration asset was changed.
Existing GA tokens remain for compatibility with the guard; absence of a preview notice
is explicitly distinguished from a sourced GA statement. Unsupported stage uncertainty
is recorded rather than used to change guard policy.

| Source | Retrieved | Disposition |
|---|---|---|
| [lifecycle](https://learn.microsoft.com/en-us/fabric/data-engineering/lifecycle) | 2026-11-14 | Runtime 2.0 explicitly GA, EOS 2028-08-31; 1.3 EOSA, tabulated EOS 2026-09-30, LTS footnote through March 2027. Preserve conservative guard exclusion; no exact final LTS day inferred. |
| [onelake-access-api](https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api) | 2026-11-14 | DFS endpoint and ADLS/Blob compatibility documented; explicit GA stage and individual create/append/flush coverage unverified. |
| [request](https://learn.microsoft.com/en-us/kusto/api/rest/request) | 2026-11-14 | POST query and management paths documented for Fabric; explicit GA stage unverified. |
| [streaming-ingest](https://learn.microsoft.com/en-us/kusto/api/rest/streaming-ingest) | 2026-11-14 | POST streaming-ingest path documented for Fabric; explicit GA stage unverified. |
| [item-management-overview](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/item-management-overview) | 2026-11-14 | Notebook, Environment and Lakehouse API support listed; explicit GA stage unverified. |
| [commit-to-git](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/commit-to-git) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [get-connection](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-connection) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [get-status](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/get-status) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [update-from-git](https://learn.microsoft.com/en-us/rest/api/fabric/core/git/update-from-git) | 2026-11-14 | Base functionality explicitly generally available; deployment-plan support Beta with beta=true. Existing code does not request Beta. |
| [create-item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/create-item) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [delete-item](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/delete-item) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [list-items](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/list-items) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [update-item-definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/update-item-definition) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [cancel-item-job-instance](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/cancel-item-job-instance) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [get-item-job-instance](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/get-item-job-instance) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [run-on-demand-item-job](https://learn.microsoft.com/en-us/rest/api/fabric/core/job-scheduler/run-on-demand-item-job) | 2026-11-14 | Current path includes jobType; source explicitly retains query-parameter path for backward compatibility. Explicit GA stage unverified. |
| [get-operation-result](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-result) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [get-operation-state](https://learn.microsoft.com/en-us/rest/api/fabric/core/long-running-operations/get-operation-state) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [list-workspaces](https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces/list-workspaces) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [get-lakehouse](https://learn.microsoft.com/en-us/rest/api/fabric/lakehouse/items/get-lakehouse) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [list-notebooks](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/list-notebooks) | 2026-11-14 | Listed method/path and v1 API documented; no preview notice for shipped use; explicit GA stage unverified. |
| [automate-git-integration-with-service-principal](https://learn.microsoft.com/fabric/cicd/git-integration/automate-git-integration-with-service-principal) | 2026-11-14 | Source now focuses on Azure DevOps and automatic/configured credentials. GitHub-specific guidance is historical and unverified by this source. |
| [runtime-2-0](https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-2-0) | 2026-11-14 | Explicit GA and current default; Spark 4.1, Python 3.13, Delta 4.2, Java 21, Scala 2.13, R 4.5.2. Delta 4.x features experimental; custom live pools preview. Exact numpy/pandas/PyArrow versions unverified. |
| [runtime-1-3](https://learn.microsoft.com/en-us/fabric/data-engineering/runtime-1-3) | 2026-11-14 | EOSA warning; Spark 3.5, Python 3.11, Delta 3.2, Java 11, Scala 2.12.17, R 4.4.1. Exact numpy/pandas/PyArrow versions unverified. |
| [user-data-functions-overview](https://learn.microsoft.com/en-us/fabric/data-engineering/user-data-functions/user-data-functions-overview) | 2026-11-14 | Python 3.11.9, public PyPI libraries, Fabric connections documented; explicit GA stage and exact bundled library versions unverified. |
| [variable-library-overview](https://learn.microsoft.com/en-us/fabric/cicd/variable-library/variable-library-overview) | 2026-11-14 | Notebook, pipeline, User data functions and Eventstream consumers documented; explicit GA stage unverified. |
| [overview](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview) | 2026-11-14 | Overall GA stage unverified; schema-aware streams, SQL operator and Spark Notebook destination explicitly preview. Emitter uses custom endpoint. |
| [create-and-use-environment](https://learn.microsoft.com/en-us/fabric/data-engineering/create-and-use-environment) | 2026-11-14 | Environment runtime selection, libraries and publish behavior documented; explicit GA stage unverified. |

## Changed files and test-first evidence

- `docs/FABRIC_PLATFORM.md`: retrieval dates, explicit versus inferred stages,
  runtime components/support caveats, feature sources and Git-sync qualification.
- `tests/docs/test_fabric_platform.py`: exactly three added test functions; every old
  assertion and fixture retained. ISO Checked dates cannot be in the future, runtime
  rows require a lifecycle source and support date, and the absent Git-sync result
  stays explicitly unqualified. No age expiry or network dependency is added.
- `docs/plans/lane_status/NR-P02.md`: source dispositions and validation journal.

Commands executed from the repository root:

| Command | Result |
|---|---|
| `make bootstrap` | Exit 0; locked docs/dbt/benchmark sync completed. |
| `uv run --locked pytest -q tests/docs/test_fabric_platform.py` before doc edits | Exit 1; **1 failed, 44 passed in 1.78s**. New absent-result test rejected the missing source-review qualification. |
| Same platform test after doc edits | Exit 0; **45 passed in 1.31s**. |
| `uv run --locked ruff check tests/docs/test_fabric_platform.py` | Exit 0; All checks passed! |
| `uv run --locked ruff format --check tests/docs/test_fabric_platform.py` | Exit 0; 1 file already formatted. |
| `uv run --locked --group docs mkdocs build --strict` | Exit 0; final build in 15.34s, no warnings suppressed. |
| `uv run --locked --group docs python scripts/check_doc_links.py --site site` | Exit 0; 0 broken links in sources and built site. |
| `uv run --locked --group docs python scripts/check_user_facing.py --site site` | Exit 0; clean. |
| `git diff --check` | Exit 0. |

CI is the full-suite gate, including the required checks; no local full-suite run
or `make check` is claimed. No gate, assertion threshold or skip was weakened.

## Unresolved live checks and follow-up findings

`Last recorded run: not run yet` remains literal and unqualified. No tenant API calls
were made. Historical successful and failed Git-sync transcripts are retained; this
source review cannot establish a current pass or repeatability. Current Git-sync,
notebook, User data functions and Eventstream execution require separate dated evidence.

Runtime 1.3 integration references still need a migration decision that accounts for
the lifecycle table's LTS footnote and EOSA detail page. Exact bundled numpy, pandas
and PyArrow versions are not enumerated by the reviewed Learn pages. The User data
functions overview establishes Python 3.11.9, not the separate portal-test claim in
integration guidance. These findings require follow-up outside this change's scope.
GitHub credential behavior needs provider-specific verification; the cited automation
article now focuses on Azure DevOps. No new endpoints are added to this inventory.

## Independent review and commits

One independent review by a smaller model (GPT-6 Luna) reopened all **28 distinct
Learn URLs** on **2026-11-14**, received HTTP 200 for each, and checked all **25 rows**
against the source pages and shipped call sites. The reviewer approved with no
factual errors or actionable findings, including the feature dispositions, Git
credential caveat and exactly three added tests. Existing guard assertions and
fixtures remain intact.

Implementation commit: `a99316e7b455d231891da27b3ad7b74095c54fa4`
(`docs: refresh sourced Fabric platform inventory`), DCO signed.
This journal is committed separately with DCO sign-off. Only the three owned
paths changed. The final platform-test rerun again passed **45 tests in 1.31s**;
final source/site link and wording checks, Ruff and format also passed.

The ready PR description records this evidence before CI starts. CI is the merge
gate; no title or description edits are planned after opening. No tags, release,
version bump or separate publishing action is part of this work.
