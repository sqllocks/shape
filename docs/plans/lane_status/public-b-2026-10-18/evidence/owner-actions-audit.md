# Owner-actions audit — October 20, 2026

Scope: read-only audit of the six Public B lane records and their own issue test plans. No product checkout, test, marker, assertion, branch or head was changed; no test or live connection was run. Apply the documentation edits below at the next mandatory main refresh, together with its required new CI. Do not push an evidence-only lane update now.

## Findings

| Issue / lane | Existing section | Assessment |
|---|---|---|
| #9 / W9-08 | `### Owner actions` at line308 | Complete for the two pending keyed live round trips; settings and pending nightly emulators are explicit. Level3 is still an explicit Owner-actions section. Optional: name both exact live node IDs. |
| #10 / W9-09 | `## Owner actions` at line88 | Complete for the new Azure Schema Registry live case and Kafka emulator. The generic existing broker-credentials bullet can be made precise with settings/test paths. Wanted4 is already approved; do not ask for that decision again. |
| #11 / W9-10 | Inline `Owner actions: no account-dependent live tests...` at line184 | No missing live item, but no explicit heading. Add the dedicated section below. |
| #12 / W9-11 | `## Owner actions` at line133 | Complete: Eventhouse URI/token live readback and nightly kustainer readback are pending. Optional: name exact node IDs and required create/ingest/query/drop access. |
| #13 / W9-12 | None; prose at lines178–182 and193–200 | Required documentation gap. Add the section below with the O-10 fixture decision, exact live module, Owner-managed settings and distinction between existing baseline assertions and future measure/RLS readback. |
| #17 / W9-16 | `## Service coverage and Owner actions` at line105 | Complete for pending REST/MinIO nightly service validation. No account-dependent live test is introduced. Optional: name the emulator node explicitly. Glue/Hive are option-acceptance only, not additional live scope. |

The issue test plans explicitly make #9, #10, #12 and #17 acceptance offline; #11 uses local fixtures and DuckDB. #13 leaves the expanded live model fixture to the Owner. These pending service actions do not add a secret blocker to the stated offline acceptance. The full suites in both kernel modes, make check and wording checks remain separate common gates under completion-plan13; this audit does not claim those gates passed.

## Required edit for W9-10

Append this standalone section near the end of `docs/plans/lane_status/W9-10.md`. The existing inline sentence may remain as historical evidence.

```markdown
## Owner actions

No account-dependent `live` test is introduced by this lane. File-based acceptance
and the marked `dbt` sample/singular-test integration use local DuckDB and require
no service-account credentials. No live execution has been run or claimed.
```

## Required edit for W9-12

Append this standalone section to `docs/plans/lane_status/W9-12.md` and point the
older Remaining work paragraph to it, rather than leaving the fixture decision
buried among superseded check results.

```markdown
## Owner actions

- Decide whether the O-10 live semantic-model fixture will gain at least one
  measure and one row-level-security role. Specify the expected measure metadata
  and the rows visible under that role. Writing roles to a live model remains
  outside this lane; no role or live fixture was deployed here.
- After the fixture is approved, use the existing marked live module
  `plugins/shape-fabric/tests/test_live_semantic_model.py` in a sempy-capable
  environment. Its Owner-managed settings are `FABRIC_WORKSPACE_ID`,
  `FABRIC_SEMANTIC_MODEL`, `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID` and
  `FABRIC_CLIENT_SECRET`; keep all values outside the repository and logs.
- The existing live function
  `test_a_real_model_profiles_with_its_relationships_and_every_table_reads`
  currently checks baseline relationships and table reads. It does not assert
  measure metadata or reads under a role. Add those read-back assertions only
  after the Owner approves the fixture and expected results; running the current
  function alone must not be reported as live validation of the new features.
  No live connection, role deployment or live query has been performed by this
  lane, and no live pass is claimed.
```

This is an Owner fixture decision and future service validation, not a request to
change scope, create roles now, weaken assertions, add skips or bypass offline
acceptance. Issue13 explicitly keeps writing live roles out of scope.

## Optional precision edits for the already present sections

- W9-08: name `plugins/shape-kafka/tests/test_live.py::test_keyed_metadata_round_trip_live`
  and `plugins/shape-eventhubs/tests/test_live.py::test_keyed_metadata_round_trip_live`.
  Keep existing settings: `KAFKA_SERVERS`, `KAFKA_TOPIC`, optional account auth in
  `KAFKA_CONFIG`; `EVENTHUBS_CONNECTION_STRING`, `EVENTHUBS_HUB`, optional
  `EVENTHUBS_GROUP`. No new live or emulator success is implied.
- W9-09: name
  `plugins/shape-eventhubs/tests/test_live.py::test_azure_schema_registry_avro_roundtrip`.
  It requires `EVENTHUBS_SCHEMA_REGISTRY_NAMESPACE`,
  `EVENTHUBS_SCHEMA_REGISTRY_GROUP` and an Entra identity allowed to register and
  read schemas in the group. It exercises the registry codec; Event Hubs broker
  settings apply to the separately pending existing broker jobs. Keep the
  approved-Wanted4 decision link below so the issue body’s old pending wording
  is not mistaken for a new blocker.
- W9-11: name
  `plugins/shape-fabric/tests/test_live_eventhouse_source.py::test_live_eventhouse_source_readback`
  with `FABRIC_EVENTHOUSE_URI` and `FABRIC_EVENTHOUSE_TOKEN`. The test creates a
  unique table, ingests and queries it, then drops it, so the Owner-provided
  identity must allow those operations. Name the separate nightly emulator case
  `plugins/shape-fabric/tests/test_kusto_emulator.py::test_emitter_rows_profile_through_eventhouse_source`.
- W9-16: name
  `tests/builtins/test_iceberg_emulator.py::test_rest_catalog_minio_roundtrip`
  and leave it pending on a nightly worker that can pull the configured REST and
  MinIO images. No real-account live test is required by its issue test plan.

## Stale status text to reconcile during that same main refresh

- W9-08 lines4–6 still call5033951 the current implementation head; current
  metadata source head was91ef620 at audit. Older current-looking prose also
  says W9-09 has not started. Keep the old evidence, but label it historical and
  make the top status point to the latest continuation/central state.
- W9-10 lines3–4 and the old Blocker and question section still read as active
  baseline/environment blockers. Later evidence supersedes them. Describe the
  current stage as reviewed implementation with common gates pending, rather
  than suggesting a new Owner decision is needed.
- W9-11 line4 says No PR opened; PR30 already exists. Replace that clause with
  the actual PR link and current gate stage at the time of the refresh.
- W9-12’s last paragraph says its corrected transcript replays remain running;
  central evidence records the corrected DRIFT_REPORT and CONTRIBUTING replays
  as passed. Supersede that old pending statement using verified current results.

Do not write a complete-gate pass or new exact-head approval until the actual
refreshed branch checks finish. Updating stale historical prose does not replace
required validation.

## Evidence consulted

- Lane records in the six corresponding `/workspace/shape-w9-NN` worktrees.
- Their live/emulator test functions and environment-variable names, read only.
- Each own issue test plan and completion-plan13 common acceptance.
- Azure Wanted4 approval dated October9:
  https://github.com/sqllocks/shape/issues/10#issuecomment-6087222698
  The approval resolves the feature decision; the current prompt’s no-skip rule
  continues to govern tests.

All read commands in this audit exited0. No branch/checkout edits or test runs.
