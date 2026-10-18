# FRZ-01 exact Nightly failure evidence

Run: https://github.com/sqllocks/shape/actions/runs/39110102198
Main: d0e64d53eecfe03b29caa5dd111598f3b9a9ace2. Frozen Shape0.9.1: d09731e59b630fac11b7127c812c47ccca8ab64f.

Status: PARKED for an Owner comparison-contract decision. No source, recording, verifier, pass rule or tolerance changed. No PR/merge/Nightly dispatch, and #20 remains open.

The initial CLI artifact download was proxy-blocked. Newly available GitHub connector download_workflow_artifact retrieved both original archives, and fetch_workflow_job_logs retrieved both original job logs. No local network policy, proxy or credentials were changed. Artifact access is now resolved.

| Artifact | ID | ZIP SHA-256 |
|---|---:|---|
| parity-profile | 12130961245 | acb9a9c231dd972d9c0cdefff7356cbcc13dd30b72e3bbd8b1ad8be42f1012af |
| parity-generate-1 | 12130276825 | 558823306dfeee8fce6fe2041cf485fc86a88aaeac50fc5313f06c8b8c00489c |

Both shard status.json files exit1, subset=false. Results name exactly four profile failures and two generation failures. No timing from these failed workloads is counted.

## Profile verifier

Verifier: benchmarks/vs_refengine/profile_1to1/verify.py.

| Workload | Recorded value | Observed value / failure |
|---|---|---|
| profile:edge/x_pq_list.parquet | __error__.category TypeError | successful nested profile; error category differs |
| profile:edge/x_pq_nested.parquet | __error__.category TypeError | successful nested profile; error category differs |
| profile:edge/x_time_only.csv | t.dow_histogram [0,1,0,0,0,0,0] | [0,0,0,0,0,0,1] |
| profile:edge/x_time_ampm.csv | t.dow_histogram [0.5,0.5,0,0,0,0,0] | [0.5,0,0,0,0,0,0.5] |

These are the exact original job117624192115 log mismatches (repeated emissions deduplicated):

```text
MISMATCH edge/x_pq_list.parquet: error category differs
MISMATCH edge/x_pq_nested.parquet: error category differs
MISMATCH edge/x_time_ampm.csv.t.dow_histogram [list_abs]: baseline=[0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0] candidate=[0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5]
MISMATCH edge/x_time_only.csv.t.dow_histogram [list_abs]: baseline=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0] candidate=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
```

Local reproduction regenerated the four60-row inputs from unchanged edge_deviations(), then ran the unchanged verifier on main through reap.py. It exits1 with exactly4 mismatches, matching the job log. Each input SHA-256 equals its authenticated recording:

| Input | SHA-256 |
|---|---|
| x_pq_list.parquet | a658ff6c7e33970e0abe991b6787b03db2cca64df56e95191202ae85c0125d7b |
| x_pq_nested.parquet | 926f799cde46e0e5cba6faaa150c3a8bc08b7040ae757333564bbea59be9a923 |
| x_time_only.csv | 6aebbd5b39f54f1ca12d8380076f8b5707f485de24ae88d0f39eb7a056860094 |
| x_time_ampm.csv | c1c846e830ae9dfa224e443b5b4b1e1356287d334536b208ac61475d8557e313 |

Nested cause: merged FIX-NESTED12c6e21 intentionally makes unsupported0.9.1 inputs work. The recorded-error equivalence branch (verify.py286-293) rejects candidate success. Restoring TypeError would undo the approved dependency.

Time-only cause: recordings captured2026-10-13 include wall-clock-dependent weekday statistics for input strings that carry no calendar date. The current run is2026-10-18. The profiler's dtparse._midnight (dtparse.py350-351) uses datetime.now() without an injected date, so the two cases retain a capture-day Tuesday component while the run has a Sunday component. This is a nonportable recording/comparison context, not changed input bytes. Per the run instruction, questionable0.9.1 recordings are parked for Owner review; none are regenerated or edited.

## Generation verifier

## capital_markets_medium

Verifier: `benchmarks/vs_refengine/domain_1to1/verify.py` clauses d and f. Baseline `0.9.1`, commit `d09731e59b630fac11b7127c812c47ccca8ab64f`.

| Column | Observed TVD | Recorded cross-seed TVD | Existing TVD tolerance | Vocabulary overlap | Failed checks |
| --- | ---: | ---: | ---: | ---: | --- |
| company.industry_name | 1.0 | 0.14200000000000002 | 0.7180961047225789 | 0.0 | tvd,vocab |
| company.sector_name | 1.0 | 0.08199999999999999 | 0.35503621636219185 | 0.0 | tvd,vocab |
| sector.sector_code | 0.9999999999999999 | 0.6363636363636364 | 1.9094750744441313 | 0.0 | vocab |
| sector.sector_name | 1.0 | 0.6363636363636362 | 2.0413147191870302 | 0.0 | vocab |

Vocabulary floor is 0.999; each listed vocabulary overlap is 0.0.

Record coherence `company.ticker+company_name+cik+exchange_code+sector_name+industry_name+market_cap_tier+founding_year+hq_state+website in sp500_constituents`: observed 0.0; existing floor 0.995.

Exact flags:
- (d) sector.sector_name: vocab
- (d) sector.sector_code: vocab
- (d) company.sector_name: tvd
- (d) company.sector_name: vocab
- (d) company.industry_name: tvd
- (d) company.industry_name: vocab
- (f) record_coherence company.ticker+company_name+cik+exchange_code+sector_name+industry_name+market_cap_tier+founding_year+hq_state+website in sp500_constituents

## healthcare_medium

Verifier: `benchmarks/vs_refengine/domain_1to1/verify.py` clauses d and f. Baseline `0.9.1`, commit `d09731e59b630fac11b7127c812c47ccca8ab64f`.

| Column | Observed TVD | Recorded cross-seed TVD | Existing TVD tolerance | Vocabulary overlap | Failed checks |
| --- | ---: | ---: | ---: | ---: | --- |
| claim_line.cpt_code | 0.9999999999999999 | 0.6533473684210527 | 0.982021052631579 | 0.0 | tvd,vocab |
| procedure.cpt_code | 1.0000000000000002 | 0.009427777777777781 | 0.039493270848342935 | 0.0 | tvd,vocab |
| procedure.cpt_description | 1.0 | 0.009427777777777781 | 0.039493270848342935 | 0.0 | tvd,vocab |

Vocabulary floor is 0.999; each listed vocabulary overlap is 0.0.

Record coherence `procedure.cpt_code+cpt_description+standard_charge in cpt_codes`: observed 0.0; existing floor 0.995.

Exact flags:
- (d) procedure.cpt_code: tvd
- (d) procedure.cpt_code: vocab
- (d) procedure.cpt_description: tvd
- (d) procedure.cpt_description: vocab
- (d) claim_line.cpt_code: tvd
- (d) claim_line.cpt_code: vocab
- (f) record_coherence procedure.cpt_code+cpt_description+standard_charge in cpt_codes

## Cause and history

- Commit `904d5d2ed46bdd2e79c0ffb243ff82821134c4d2` (Owner-authored, 2026-10-12) replaced the 49 procedure codes/descriptions and capital-markets sector/industry labels.
- `docs/plans/OWNER_DECISIONS.md:173` explicitly requires the synthetic taxonomy; line183 requires synthetic procedure codes.
- `plugins/shape-domains/README.md:47-58` documents these replacements and says to recreate fixtures on upgrade from0.9.1.
- Read-only Arrow comparison of commit904d5d2 parent versus current files found identical schemas, row counts49/11/123, and equality of every column except the replaced text/nested labels.
- The report exhibits exactly those vocabularies: candidate company sectors/industries are synthetic; old labels have candidate frequency0. Procedure and claim_line codes are SP-NN, never the old codes.
- `Pools` in `benchmarks/vs_refengine/domain_1to1/verify.py:163-185` deliberately reads baseline-release reference pools. Categorical vocabulary checks at426-452 compare against baseline output plus those pools; coherence at505-519 uses the same baseline datasets. Their rejection is consistent with their specified exact vocabulary/coherence checks.

## Classification and recommendation

This is an incompatibility between the intentionally changed reference data and the frozen0.9.1 equivalence target. The recordings are authentic old outputs rather than malformed recordings, and the verifier is enforcing its existing rules. Restoring the old labels would contradict the Owner decision; silently accepting or re-recording the new labels would change the comparison contract. Park the generation portion with this evidence for an explicit Owner decision on how cross-release intentional reference substitutions should be compared. Keep the existing recordings and gates unchanged meanwhile.

## Owner question, options and recommendation

How should frozen0.9.1 comparisons handle (1) newly supported nested inputs, (2) capture-date-dependent time-only statistics, and (3) Owner-mandated synthetic reference substitutions?

Options: retain strict equivalence and leave these workloads red; authorize an explicit comparison contract for these noncomparable cases with unchanged numeric tolerances/floors and preserved recordings; or authorize a separately justified new baseline policy. Recommendation: define an explicit tested contract for unsupported-baseline inputs, capture clock context and intentional dataset substitutions. Do not restore obsolete errors or removed reference labels, silently re-record, or weaken existing thresholds. All such contract changes require Owner authorization under the prompt's no-pass-rule-change rule.

## Commands and verification limits

- Both connector artifact downloads and both job-log reads succeeded; archive file SHA-256 values above were independently calculated.
- Fixture generation through locked uv and reap.py exits0.
- Unchanged two-time-fixture verifier exits1 (2 mismatches).
- Unchanged all-four-fixture verifier exits1 (4 mismatches).
- Input-digest comparisons all true. Read-only history and reference-table comparisons recorded in the generation section.
- This is investigation evidence, not a passing FRZ-01 acceptance run. No new benchmark speed claim or live run.
