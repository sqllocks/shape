# FRZ-01 harness audit — Owner ruling 2026-10-26
Verdict: PASS under the rule permitting replay of clock-derived values only; 2 A fixture cases, 0 B cases; no production code change.
A1: `edge/x_time_only.csv` contains times without dates (`profile_1to1/datasets.py:506-510`); its missing year/month/day default to the clock.
A2: `edge/x_time_ampm.csv` likewise contains only AM/PM times (`profile_1to1/datasets.py:513-514`); its missing year/month/day default to the clock.
Replay changes those defaults only when `today is None` (`capture_date.py:105-111`); explicit supplied defaults are preserved.
Parser consumer 1: `_parse_datetime_string` fills a time-only date from `_midnight(today)` (`dtparse.py:335-336,350-351`): A in both fixtures.
Parser consumer 2: `guess_format` uses that default for an intermediate parsed value (`dtparse.py:428-430`): A for missing fields, never replacement of supplied fields.
Both consumers reach `_dateutil_parse`, which copies supplied fields into `repl` and uses `default.replace(**repl)` (`dtparse.py:369-382`); input dates/times win.
Absolute ISO/delimited/named-date paths also remain unchanged; no B case or resulting parity failure was found.
The only replay entry points are verification (`profile_1to1/verify.py:304`) and timing (`profile_1to1/bench.py:118`), restricted to the two authenticated fixture digests.
Tests: `test_replay_preserves_data_derived_dates` checks exact absolute ISO, leap-day, delimited, named-month and datetime values through mixed and column parsing in both contexts.
Tests: `test_replay_changes_only_clock_derived_default_date` checks host-to-replay date changes, unchanged time-of-day, explicit defaults and restoration for 24-hour/AM-PM values in both contexts.
Targeted output: `tests/benchmarks/test_capture_date_replay.py`: 31 passed in 6.52s; one kernel, no full suite or Nightly dispatch.
Aliasing remains label-only and fail-closed (`reference_names.py:117-168`); unexpected capture-context fields are rejected (`capture_date.py:53-59`), with existing negative tests retained.
No tolerance, pass rule, recording, input fixture or production code changed; no re-recording.
