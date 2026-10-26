# FRZ-01 — post-merge acceptance condition

Implementation: https://github.com/sqllocks/shape/pull/48, merged as
`682759841dcffd56dff62f74dc90ac18286cc600`.

Acceptance is bound to [this Nightly run](https://github.com/sqllocks/shape/actions/runs/40272534667), dispatched at
`2026-10-26T05:07:08.557663Z` with event `workflow_dispatch`, ref `main`, and the `confirm` input
empty/omitted. Its head is `682759841dcffd56dff62f74dc90ac18286cc600`, containing the implementation merge.
This identifies the actual post-merge run; it asserts no successful result. Earlier
scheduled runs do not provide this acceptance evidence.

Closure requires all seven jobs in that same run to complete with `success`:

- `parity-suite (profile)`
- `parity-suite (stream)`
- `parity-suite (generate-1)`
- `parity-suite (generate-2)`
- `parity-suite (generate-3)`
- `parity-publish`
- `sqlserver-e2e`

Queued, running, failed, skipped, timed out and cancelled jobs are not passes.
Every recorded-value comparison and threshold remains unchanged; no baseline is re-recorded.
The merged parity artifact must identify the same head, report the full suite with no
missing or failed workloads, and contain exactly the two authenticated refusals below.
Its passed count equals its required count minus those two refusals. The recording job
must remain skipped.

The authenticated recorded-error workloads remain separate from passed workloads:

| Workload | Category | Authenticated reason |
|---|---|---|
| `profile:edge/x_pq_list.parquet` | NOT COMPARABLE | Shape 0.9.1 recorded TypeError for a list-valued Parquet input; no baseline values exist. |
| `profile:edge/x_pq_nested.parquet` | NOT COMPARABLE | Shape 0.9.1 recorded TypeError for a struct-valued Parquet input; no baseline values exist. |

Neither refusal receives pass or timing credit. All value-producing workloads retain their
original comparisons. No live-account success is asserted here.

The separate acceptance PR may be ready with `Closes #20` while this Nightly and its own
checks run. Merge it only after the seven named Nightly jobs succeed, every check triggered
by the acceptance PR is green on its exact head containing current main, and independent
review approves that head. Record observed results and links in the PR body and lead scratch
state; this permanent condition record needs no success-status edit. Issue #20 closes only
through the merged acceptance PR.
