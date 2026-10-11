# Post-launch backlog (written 2026-10-11)

What to build first after Shape 0.9.1 is tagged, in order. Sources: the open issues of
`sqllocks/shape` (#1 to #20), the lane status files on `origin/lane/*` (read 2026-10-11), §11 and
§13 of `docs/plans/COMPLETION_PLAN.md`, and the gaps found while writing `runbooks/live/`.
Nothing here changes a decision, gate or tolerance; items that need one say so and stop there.

**Builder tiers.** `haiku`: one or two files, a fixed recipe, no design choice. `sonnet`: a work
package with tests, docs and several files, specified by an issue. `cursor`: an existing lane
that needs a person in the loop (review, CI babysitting, merge, an Owner decision applied).

| # | Item | Why first | Tier |
|---|---|---|---|
| 0 | Tag `v0.9.1` from `release/0.9.1` once REL-091 (#19) is green, publish the wheels (core on PyPI; the twelve plugins too, or at least as release assets) | Everything below is dated from the tag; the live playbooks cannot install 0.9.1 from PyPI today (0.9.0 only, no plugins) | cursor |
| 1 | FRZ-01 (#20): `docs/plans/wp/FRZ-01.md` | Nightly and `bench-quick` are red every night until the reference engine is out of CI; it is the only red that is not a product bug | sonnet (build), cursor (the one-time record and the Nightly dispatch) |
| 2 | W9-01 (#2) PostgreSQL and MySQL live profiling: `lane/W9-01` is implemented and "awaiting lead CI and review" | Closes the largest gap in `runbooks/live/postgresql.md` and `mysql.md` (profiling needs an export today); three other lanes (W9-13, W9-14, W9-15) are based on it | cursor |
| 3 | Kafka emitter: let `--sink-config kafka.config=file://PATH` pass producer settings (SASL_SSL) through `sink_config_keys` | `shape emit` cannot reach Confluent Cloud from the command line at 0.9.1 (`runbooks/live/kafka.md`, Gaps); a two-file change with a contract test | haiku |
| 4 | W9-16 (#17) Apache Iceberg: `lane/W9-16` reports "implementation complete; local gates finished; ready for lead CI and review" | Finished work waiting on review; depends on W8-07's reader (item 9) being accepted first or rebased | cursor |
| 5 | W9-07 (#8) Delta table features on write: `lane/W9-07` "implemented; lead CI acceptance pending", no open question | Finished work waiting on CI; opt-in only, low risk | cursor |
| 6 | W9-08 (#9) Kafka and Event Hubs message metadata: `lane/W9-08` "implementation complete; updated local gates pass; lead CI pending" | Finished work waiting on CI; pairs with item 3 for the streaming playbooks | cursor |
| 7 | W9-10 (#11) dbt II: `lane/W9-10` "implementation ready for review; common acceptance blocked" | Finished work; the blocker is the common acceptance (full suite, `make check`), a CI run away | cursor |
| 8 | W9-02 (#3) Snowflake and Databricks live profiling: not started | Second largest playbook gap (`snowflake.md`, `databricks.md` export first); the sinks exist, the sources follow W9-01's pattern | sonnet |
| 9 | W8-07 (#1) nested columns in profile and the clean Delta exit: `lane/W8-07` "wip, pushed for lead verification", 4 failures and 36 setup errors in `make check` on its base | Fixes the one open bug of the issue tracker (any nested column fails `shape profile`); W9-05, W9-06 and W9-16 depend on it | sonnet (fix the 4 failures) then cursor (accept) |
| 10 | W9-11 (#12) Fabric Eventhouse source: `lane/W9-11` "implemented; acceptance blocked by baseline typing and offline-suite blockers" | Closes `fabric.md` step E2 (Eventhouse profiling); the blockers are environmental, not design | sonnet (unblock) then cursor |
| 11 | W9-12 (#13) Power BI measures and row-level security: `lane/W9-12` "implemented; pushed for lead CI; local Delta fixture setup blocked by environment network policy" | Finished work; needs a CI run with network for its fixture | cursor |
| 12 | W9-03 (#4) type fidelity on write: `lane/W9-03` "implementation ready; byte-corpus acceptance awaiting Owner" | Finished work; one Owner decision on the byte corpus (the same question that holds W8-04) unlocks it | cursor, after the Owner answers |
| 13 | Owner decisions that unblock three lanes: W9-13 (#14) BigQuery decimal mapping (`NUMERIC` vs `BIGNUMERIC` for legal Arrow decimals BigQuery cannot create), W9-14 (#15) Redshift `COPY` literals for the S3 location and IAM role, W9-15 (#16) Oracle driver API (`fetch_df_batches` on python-oracledb 3.0 and the required types) | Each lane stopped at a specification question with evidence; no code is useful until answered | Owner, then sonnet to finish each lane |
| 14 | W9-04 (#5) constraints on write, W9-05 (#6) nested II, W9-06 (#7) native nested types, W9-09 (#10) formats II: not started | The remaining platform-coverage packages; W9-05 and W9-06 wait for item 9 | sonnet |
| 15 | Live playbook runs (`runbooks/live/README.md` order) and the docs' "Needs a Fabric account. Not run in CI" transcripts (`docs/plugins/*.md`, `docs/SINKS.md`, `docs/EMIT.md` placeholders marked `owner: ... supply the transcript`) | Turns the playbooks into evidence and fills the documented examples with real output; needs the accounts, not design | haiku (run and paste), with the Owner's accounts |
| 16 | Tracker hygiene: rows 138 (W5-08, the Anonymeter `numpy<1.27` pin against T-07's `numpy>=2.0` floor), 145 (W7-02 item 5, the `_send` retry of `FileNotFoundError`), 147 (W7-05, no lane status), 150 (W7-01, the live Git sync test needs O-02 and O-12), 152 (W8-01, no lane status) | Five `wip` rows that are each one small action: a decision to drop the Anonymeter adapter or relax nothing, a three-line fix in `streaming/emit/runtime.py`, two status files, one owner secret | haiku (the fix and the status files), Owner (the secrets, the Anonymeter call) |
| 17 | W8-04 (#567) byte-identical output per engine version on every platform: held with W1-15 after the cross-CPU proof failed | A stability promise users will ask for after launch; needs an Owner decision on the corpus (item 12) and a cross-CPU design, not a quick fix | sonnet, after the decision |
| 18 | W8-05 (#568) vault management through the bridge: blocked on W5-03's vault and W1-11 | Last; its dependencies are not built | later |

Not in this list: the §13 packages marked `filed at start` that have no lane and no issue yet
(they get their issue when a lane starts, §13), and anything that would change §2's decisions
(escalate per §0.4 instead).
