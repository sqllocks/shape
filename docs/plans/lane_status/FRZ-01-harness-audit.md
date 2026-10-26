# FRZ-01 harness audit — 2026-10-26
Verdict: PARTIAL; the literal runtime-clock-only replay requirement is not satisfied.
Aliasing PASS: `reference_names.py:117-136` authenticates bijective label lineage and preserves every other reference-row value; `:160-168` copies frames and maps only scoped labels.
Registry: `reference_name_aliases.json:1-18` binds source/target digests and explicit name associations; no observed numeric values or frequencies fit the mapping.
Call site `domain_1to1/verify.py:700-703` normalizes both sides/pools before unchanged comparisons; unknown names raise at `reference_names.py:145-146`.
Coverage: `test_reference_name_aliases.py:58,79,99` rejects unknown/permuted aliases and numeric, frequency, label-coherence and charge-pair mismatches.
Replay FAIL (literal requirement): `capture_date.py:105-111` replaces the implicit parser date, not merely an output runtime timestamp; `dtparse.py:428-430` uses it to parse values.
Call sites `profile_1to1/verify.py:304` and `profile_1to1/bench.py:118` apply replay before profiling/timing; explicit dates remain unchanged.
`test_capture_date_replay.py:37` explicitly tests frozen-date equivalence on other host dates; `:94` confirms altered distributions still fail. These do not prove runtime-fields-only behavior.
Fail-closed improvement: `capture_date.py:53-59` now rejects unexpected top-level context fields as well as mismatched provenance/workloads.
New `test_capture_date_replay.py:145`: unexpected-date controls were 1 failed/2 passed before the guard; both targeted files now report `30 passed in 5.33s` (Python kernel only).
Unknown explicit replay requests fail at `capture_date.py:51-52`; unrelated workloads intentionally receive no replay at `:99-101`, tested at `test_capture_date_replay.py:60`.
Owner decision: retain authenticated parser-date replay as an explicit exception, or require runtime-fields-only and accept the original time-only comparison failures; recommend explicit clarification, with no tolerance, recording or pass-rule change.
No full suite, second kernel run, or CI dispatch was used for this audit.
