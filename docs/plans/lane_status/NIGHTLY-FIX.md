# NIGHTLY-FIX — the first full Nightly run on build/main-plan (lane/NIGHTLY-FIX)

Status: **in progress.** Failing run: 37043012563 (commit 8f4a0c1). No gate, tolerance, decision or
assertion was changed; nothing was skipped, xfailed or retried as a "flake".

| Job | Root cause | Fix | Passing run |
|---|---|---|---|
| sqlserver-e2e | `gpg --dearmor -o` needs a TTY on the runner (`cannot open '/dev/tty'`), so the hand-built apt repo step died | install the repo with Microsoft's `packages-microsoft-prod.deb` for the runner's Ubuntu version, then `msodbcsql18 unixodbc-dev` | 37066796113 |
| emit-rate-soak | the job installed only `.[dev]`; the soak plan uses the `retail` domain, which comes from `plugins/shape-domains` (`DomainNotFoundError`, 0.8 s) | `-e plugins/shape-domains` in the job; the test, its 3600 s duration and its tolerance are unchanged | pending |
| kafka-e2e | same missing `shape-domains` (4 emitter tests); the other 10 tests had passed | `-e plugins/shape-domains` | 37066796113 |
| eventhubs-e2e | same missing `shape-domains` (6 emitter tests) | `-e plugins/shape-domains` | pending |
| fabric-emit-e2e | (1) same missing `shape-domains`. (2) after that, the Eventhouse mapping command was rejected by the Kusto emulator (`JsonMapping ... couldn't be deserialized`): the mapping JSON is a KQL string literal and its own backslashes (the quotes inside each `$["col"]` path) were not doubled, so the service read broken JSON. A product bug in `create_mapping_command`. (3) `test_eventstream_idempotency_key` compared the wire key as `b'order_line/0'`: the Event Hubs test reader decoded property keys but not values. A bug in the test helper `shape_eventhubs.testing.read_raw`, not in the emitter | (1) `-e plugins/shape-domains`; (2) double backslashes before escaping quotes in `create_mapping_command`, and the unit test now resolves the KQL escapes the way the service does before parsing the JSON; (3) decode bytes property values in `read_raw` | pending |

`benchmarks-full`: not touched by this lane; see the run for its result.
