# Public B current recovery state

Updated 2026-10-26 after PR48 merge and authenticated Nightly dispatch. Unattended run: never end to wait or ask. After restart read this file and `/workspace/attachments/e304682d-bc67-494c-8b2e-3d8b1436dda0/5-NEW-WINDOW-shape-public-B.md`. Latest Owner chat overrides original local-full and serial-lane rules. Scratch `/workspace/shape`, branch `lead/public-b-2026-10-18`; DCO every commit and push immediately.

## Delivered and current main

**8/9 assigned issues merged (88.9%); 1/9 remains (11.1%): #20 acceptance.** No implementation is parked. Supporting certificate PR40 is outside the denominator. No live-account success is claimed.

[PR48](https://github.com/sqllocks/shape/pull/48) MERGED at **2026-10-26T05:06:58Z**, merge/current main **682759841dcffd56dff62f74dc90ac18286cc600**. Exact independently approved source **b9e9f50cb611886afabb4cfa1a73e01998aa2525**; metadata_oct25 reviewed full Wanted/security/scope and formats_oct25 reviewed broker/FRZ preservation. All seven triggered source workflows SUCCESS, 42 successful checks and only two structural publish skips. Source acceptance is complete; merged-main CI is separate and not yet claimed green.

GitHub issue API verified #10 CLOSED05:06:59Z, #17/#6/#14 CLOSED05:07:00Z through48. Issues9/12/13/11 were already merged. #20 remains OPEN:48 uses Refs20, not closure.

| Source workflow | Green run |
|---|---|
| CI (27/27 jobs) | https://github.com/sqllocks/shape/actions/runs/40264320674 |
| Wheels (10/10 jobs) | https://github.com/sqllocks/shape/actions/runs/40264320723 |
| Documentation | https://github.com/sqllocks/shape/actions/runs/40264320625 |
| DCO | https://github.com/sqllocks/shape/actions/runs/40264320666 |
| Security | https://github.com/sqllocks/shape/actions/runs/40264320751 |
| Container | https://github.com/sqllocks/shape/actions/runs/40264320624 |
| action-selftest | https://github.com/sqllocks/shape/actions/runs/40264320807 |

Owner speed directive consolidated all five remaining implementations into48, replacing every old serial stage/hold. Do not refresh, restart or merge old PRs31/34/42/43/47; preserve their original proofs as history. Root solely handles merges and future integration/acceptance source edits.

## Current acceptance task

Root dispatched [Nightly40272534667](https://github.com/sqllocks/shape/actions/runs/40272534667) with **empty confirm**, 2026-10-26T05:07:08.557663Z; created05:07:09Z, event workflow_dispatch/main, exact head **682759841dcffd56dff62f74dc90ac18286cc600**. This is the first qualifying candidate run; currently PENDING, never claimed green. Initial five parity shards and sqlserver-e2e queued; record-baseline confirmed SKIPPED. Ready [acceptance PR50](https://github.com/sqllocks/shape/pull/50) is open from merged main, exact signed/pushed clean source **0ebed21ff09eca3ecab4b0132b0347ba82eaa3c3**. metadata_oct25 independently APPROVED this exact head. Its44-line document records criteria/provenance without a pending-green claim; locked public/whitespace checks pass. Root owns `/workspace/shape-frz-acceptance`, branch `lane/FRZ-01-acceptance`; all triggered PR50 gates run in parallel with Nightly and remain required before root merges.

Close20 ONLY through that subsequent acceptance PR after BOTH its exact-head/current-main/independently reviewed full gates and the qualifying main Nightly succeed. Required Nightly jobs: parity-suite(profile,stream,generate-1,generate-2,generate-3), parity-publish, sqlserver-e2e, all SUCCESS. Recording must remain SKIPPED. Inspect parity-suite results/nightly artifacts: exact run head, clean tree/full_suite, every shard exit0, no missing or failed workloads, exactly TWO authenticated not_comparable ITEMS with implementation=shape and list/struct workload names, passed=required-2. `nightly.IMPLS=("shape",)` and `run.expected_ids` return only shape; baseline copy aggregation does not double-count the category. Manual run is separate from the scheduled seven-day streak. No recording or comparison policy changes.

formats_oct25 now watches Nightly actual job steps and failure-level live annotations; no raw archives. metadata_oct25 watches main. Root owns scratch except explicit delegation: formats updates state/report for this milestone, DCO and immediate push. Availability through2026-10-26T12:00UTC; renew before bound.

## Active small emulator repair in acceptance PR50

DO NOT merge the original doc-only 0ebed21 head yet. Actual dispatched Nightly has two independent emulator failures: own Iceberg startup fails because upstream withdrew both minio/minio and minio/mc distributions; inherited Oracle tests lack their driver/service in databases-e2e. The seven Owner #20 jobs are separate: SQLserver/stream/generate-1/generate-2 SUCCESS, profile/generate-3 running, publisher waiting. Never claim the overall Nightly green while independent jobs failed.

Root assigned iceberg_oct25 exclusive edits in `/workspace/shape-frz-acceptance` to ci/emulators/docker-compose.yml plus minimal emulator Dockerfile(s), optionally W9-16 lane evidence. Build the SAME official source releases pinned to server commit0d7408fc9969caf07de6a8c3a84f9fbb10a6739e and client b00526b153a31b36767991a4f5ce2cced435ee8e; original public distributions and same-tag Quay/archive binaries are unavailable (actual hosted pull denial; official archive410). REST fixture apache/iceberg-rest-fixture:1.10.0 manifest remains available and unchanged. Author is testing source builds/actual unchanged targeted Iceberg emulator acceptance before root commits. No claimed green yet, no new skips/assertion/gate changes. Docker skill CA/proxy rules apply. Root will combine this tiny startup fix into50, rewrite its title/body around final scope, bind a NEW independent exact-head review and let fresh full hosted CI run. This avoids a separate serial fix PR and another base refresh. No author commit/push; root handles one signed/pushed source head after actual targeted green.

Inherited Oracle failure is LeadA PR39/6bc8970, ancestor of pre48 main314749d; test unchanged across48. Actual3fail+3cleanup errors/21passed because existing databases-e2e selects all emulator tests but starts only postgres/mysql and does not install Oracle extra. Metadata read-only verified current LeadA49 has no Oracle/Nightly fix. External Owner/LeadA action/provenance recorded in questions.md, signed/pushed92945f9; do not edit their branch/issue or weaken selection. Metadata watches merged-main all workflows; formats watches Nightly; root monitors PR50 via persistent functions.exec cell423 (old48 watch377 finished). Helpers outside repo: nightly_gate.py verifies dispatch/main/implementation ancestry/seven jobs; verify_nightly_artifact.py validates downloaded parity-suite ZIP in place; use standard GitHub artifact connector then download_file with returned Sediment file_id. Never print temporary download URLs or raw result archives; remove inspected ZIP afterward. No full local suites.

## Included source history

| Issue | Original PR/head | Delivery |
|---|---|---|
|10 formats|34 / 9b3e7375694514faeefa779c11f05bb13e1a8e2b|MERGED through48|
|17 Iceberg|31 / 24a145795a0f33f95d125fa0a7123f76b959d780|MERGED through48|
|20 FRZ|42 / 53bc24958788830669a00d47e9048c76418a475d|Implementation merged48; acceptance OPEN|
|6 nested|43 / e5d6dd3d8351854b7967f145f5c7aeb6c1c32f5e|MERGED through48|
|14 BigQuery|47 / 8228f87e699bb3524bf26f4fa4fefc6d0449856e|MERGED through48|

Earlier delivery:9/PR32→35addfc4c310b85a62cbe7f315e1d50fb8e426f8;12/PR30→5c6d46ff1703db9fd2307bc4ebd4fb087e9af733;13/PR33→404fed823b271525b8802254312ded28f1c5aaa8;11/PR29→90fbff999bc95e255b3dbd90b76278441a735b41. Source/merged green links and original red/green proof remain in report.md/lane records. Canceled historical main5c/53 CI is never a pass. Supporting cert40→53d3d0c1a740fcca0665c216c3ebdabaaac95e49 is excluded from assigned denominator.

## Combined targeted proof and review

Lane `docs/plans/lane_status/public-b-integration.md` on48 contains PRlink, decision row and evidence. Original complete Wanted lists and tests-first proofs remain in W9-09/W9-16/FRZ-01/W9-05/W9-13 records.

- Broker conflict: binarydecoder ORexplicitJSONnestedTrue. Existing223+new6matrix GREENeachkernel; each discarded merge alternative RED (1fail/5pass,2fail/4pass).
- NewCloudSource._plan depth:12RED eachkernel→90GREEN eachkernel; sharedvalidate_depth0..64 beforeconnect, unknownoptions closed.
- BQ shared publicdepth default8/0/1/64:92GREEN eachkernel; sampling/declaredtypes/provenance preserved, no BQ source refactor.
- Iceberg profile_source omitteddepth:4RED/2defaultpass eachkernel→136GREENeachkernel; oneforwardline preserves table/namespace snapshot/rowcounts.
- BQ async job fragment leak:9RED/4controls eachkernel→40securityGREENeachkernel. New embedded repeatedscheme3-second timeout REDboth→linear whole-token callback GREEN. Final security including30existing query/adversarial controls70GREENeachkernel.
- Ruff/format2148files, configuredmypy711sourcefiles, publicwording clean; final security lint/format/mypy repeated after repair. DB conformance9GREEN. ActualCLIlist+doctor exit0/191registrations; docs EXACT generated192/193line transcripts (Markdownfence excluded). 381lockedpackages, no existing locked-version change. Freshintegration.venv fromintegration paths, native build verified; every test via subreaper and uvlocked/no-sync. No localfull suite.

Independent reviewers verified all original Wanted/security/scope, unchanged FRZ recordings/thresholds/date provenance/name aliases, child privacy, shared DB/Oracle/cloud/BQ/Iceberg depth, safe URI handling, additive requirements/docs/lock and DCO. No gate/assertion/test weakened, skipped, deleted or xfailed.

Additional combined repairs, all tests-first and reviewed:

- Strict BQ staging privacy: original Windows private assertion failed; explicit CPython bootstrap grants removed only on fresh empty staging, then existing helper regrants current identity. 3 ordering/fail-closed +6 bootstrap controls RED each kernel→101 BQ GREEN each; actual Windows strict database job succeeded on final source. No POSIX/private assertion weakened.
- Architecture guard: direct plugin subprocess produced two RED hits each kernel; bootstrap logic moved into existing core restrict_to_current_user(remove_bootstrap=False), BQ True for fresh staging. Default other callers unchanged; no generic runner/allowlist. 170 BQ+scale+trust GREEN each kernel.
- Inherited durable cancellation race: deterministic1 RED each kernel→41 job tests GREEN each, existing assertion/timeouts preserved. OS lock covers complete read-modify-write; in-memory behavior unchanged.
- Optional dependency boundary: eight async BQ cases failed in minimum core environment; function AST/parameter assertions relocated unchanged into mandatory plugin test_bigquery_jobs.py. Six core redactor controls remain active with plugin blocked. Baseline8RED/6pass→6coreGREEN and184 BQ/scale/trust/core GREEN each kernel. No product, CI environment, guard, marker or skip change.

Actual combined289 targeted acceptance regenerated only candidate seed1042 for healthcare/capital_markets, authenticated all five original baseline seed outputs, and passed all original clauses a–f:80/80 and64/64 columns. Both actual time-only/AMPM fixtures PASS every original field/zero mismatches. Separately two recorded TypeErrors NOT COMPARABLE/exit3, never passes/timing credit. Existing baseline0.9.1 env Arrow26 differs candidateArrow25; no whole-environment or timing claim. No recording/fixture/reference/env/source changes. Numerical/benchmark/broker product bytes unchanged from289 through finalb9; no repeat run needed.

## Owner comparison decisions and pending live

NOTCOMPARABLE ONLY authenticated recorded0.9.1 error/no-values workloads: `profile:edge/x_pq_list.parquet` (list TypeError), `profile:edge/x_pq_nested.parquet` (struct TypeError). Reviewed registry `benchmarks/vs_refengine/comparison_contract.py`. Report separately, NEVERpasses/timing credit. All recorded-value comparisons unchanged; no rerecording. Four repaired value workloads actualPASS: healthcare80/80columns, capital_markets64/64, time-only/AMPM0mismatches each. Name normalization uses authenticated reference lineage, never values/frequencies/order-fitting. Capturedate replay independently authenticated Oct13 source/run context before computation; no output masks or explicit-date changes. Full details original FRZ record.

BQ Ownerdecimal0<=scale<=precision beforeconnect, columnnamed; LeadA45 sourceinterface merged, dependency resolved. SourceusesactualSDKconstraints/decimalTargetsNUMERIC+BIGNUMERIC, no STRINGfallback; account tests remain pending. Iceberg/BQ each add2 registrations, newmain37 adds2: actual combined191, verified.

No implementation parked. Pending external live actions, never claimed passing: broker/EventHubs/SchemaRegistry accounts/emulators;Eventhouse emulator/account; semantic measure+rolefixture expectedmetadata/visibleRows; IcebergREST/MinIO images; BQO14generate→write→profile/datasetidentity accounts. dbt8actualDuckDBoffline acceptance passed, no account requirement. Premium and LeadA branches/issues are not touched; narrow alreadymerged shared-interface changes only where own Wanted requires.

## Environment and hard stops

Cloudskill c5/cloud-environment-runtime read on recovery; current enforcedunrestricted HTTP/networkpolicy VPNfalse; GHready and actual gitlsremote+ghAPI exit0. Preserve proxy/CA/auth, never print tokens/env/secretfiles/changegitconfig/globalsettings. Transient scratch503 recovered immediately; no persistent credential failure. Persistent401/403/failedpush hardstop only after actual5/10/20min gitlsremote+ghAPI retries allfail; do useful local work between. Logs404/blob403 not auth failure if APIs work.

Use `/workspace/public-b-validation/test-env.sh`, uv0.8.17locked; no pip into projectenv, no concurrentuvsync or sharedvenv mutation. Every test `python /home/agent/bin/reap.py`. No old full workers; do not revive historical queueJSON/receipt watchers. Last zombies12; hardstop>1000. Other hardstop: own mergedmain red and cannot fix one smallPR—revert ownmerge viaPR thenstop; allissuesmerged/parked→finalreport. Never tags/releases/publish/directprotectedpush/selfhosted/bannednames/loosenedgates.

User wants progress updates without asking: meaningful updates aboutminute, fullcountsabout5min. User-facing time **America/New_York**, internal machine timestamps explicitlyUTC. Keep report INTERIM until all9merged/parked or genuinehardstop; then finalreport+same chatsummary. Never end towait/ask.

Failure log access: CLI log download hits sidecar403 on Azureblob, not GHauth; do not bypass proxy/routing or print signedURLs onstderr. Installed standard GitHub connector `github_fetch_workflow_job_logs` with configured Primarysqllocks link_id link_6abacc0c2f408191aff9ab5aec95acde works and supplied actual traceback. Use connector for future joblogs, filter only failure text; no rawlog archives. GitHub API/credentials remain working.
