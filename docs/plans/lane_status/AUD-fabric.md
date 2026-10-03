# AUD-fabric: audit of the Fabric, Event Hubs and Kafka plugins (lane/AUD-fabric)

Status: **in progress.** Area: `plugins/shape-fabric/**`, `plugins/shape-eventhubs/**`,
`plugins/shape-kafka/**`, and the `docs/plugins/**` pages for them. Every finding below was
reproduced in this session; the issue gives the reproduction, the expected and the actual result.

## Findings

| # | sev | file | defect | issue | fix |
|---|---|---|---|---|---|
| 1 | high | shape-fabric `recording.py` (`_PATTERNS`) | scrubber leaves passwords holding `}`, JSON-quoted and quoted secrets in tapes | #411 | |
| 2 | high | shape-fabric `sinks.py` (`connection_string_for`) | `user:pw@host:port` copied into `Server=`; password then shown | #415 | |
| 3 | high | shape-fabric `eventhouse.py` / `kusto.py` (`prepare`) | fixed-table emitter ingests one table through another's JSON mapping | #419 | |
| 4 | high | shape-fabric `semantic_model.py` (`_measures`) | duplicate measure names (retail) | #425 | **lead**: measures are compared with the baseline by `fabric_commands_1to1/verify.py` |
| 5 | medium | shape-fabric `sqldb.py` (`prepare_table`) | truncate/replace commit before the insert | #429 | **lead**: fixture `sql_replace_with_key.json` pins the commit after `DROP TABLE` |
| 6 | medium | shape-fabric `onelake.py` (`parse`) | `..` / `%2e%2e` escape the item | #432 | |
| 7 | medium | shape-fabric `fabric_api.py` (`_await`) | bearer token sent to any `Location` host | #434 | |
| 8 | medium | shape-fabric `fabric_api.py` (`_await`) | `Cancelled` polled 180 s then "timed out"; `Retry-After` ignored | #436 | |
| 9 | medium | shape-fabric `fabric_api.py` / `commands.py` | unexpected answer exits 2 with `'id'` | #438 | |
| 10 | medium | shape-fabric `_storage.py` | first account's filesystem reused for other accounts | #440 | |
| 11 | medium | shape-fabric `recording.py` (`TapeTransport`, `TapeConnection`) | tapes change exception classes; failed commit not recorded | #442 | |
| 12 | medium | shape-fabric `sinks.py` (`SqlServerSink`) | URI query ignored when `connection_string` is given | #443 | |
| 13 | medium | shape-fabric `eventhouse.py` (`_client`) | later emit's token/credential/retries/timeout ignored | #444 | |
| 14 | medium | shape-fabric `kusto.py` (`kusto_type`) | `uint64` → `long` overflows; `duration` → `string` | #445 | |
| 15 | medium | shape-fabric `_tsql.py` (`column_type`) | `max_length` over the limit, non-integer, negative scale | #446 | |
| 16 | medium | shape-fabric `onelake.py` | workspace not decoded/checked; double decoding; URLs not encoded | #447 | |
| 17 | medium | shape-kafka `source.py` | bounded read returns messages produced after it began | #351 | |
| 18 | medium | shape-eventhubs `source.py` | bounded read returns events enqueued after it began | #353 | |
| 19 | medium | shape-eventhubs `source.py` | `max_messages` counts earlier reads of the same source | #354 | |
| 20 | medium | shape-eventhubs `source.py` | a body that is not UTF-8 aborts the read | #355 | |
| 21 | low | shape-eventhubs `emitter.py` | oversized event after the first raises a raw `ValueError` | #356 | |
| 22 | low | shape-fabric `notebook.py` | domain and seed pasted into generated code unquoted | #448 | |
| 23 | low | shape-fabric `_tsql.py` (`normalize_connection_string`) | `Encrypt=Strict` downgraded; settings dropped | #449 | |
| 24 | low | shape-fabric `fabric_api.py` | paging loops on a repeated continuation token | #450 | |
| 25 | low | shape-fabric `fabric_api.py` | ids go into the URL path unescaped | #451 | |
| 26 | low | shape-fabric `_auth.py` / `auth.py` | empty token sent as `Bearer None`; fallback hides the first failure | #452 | |
| 27 | low | shape-fabric `keyvault.py` | `$` anchor accepts a trailing newline | #453 | |
| 28 | low | shape-fabric `onelake.py` (`landing_zone`) | invalid dates, Unicode digits, `hour=1.9` accepted | #454 | |
| 29 | low | shape-fabric `recording.py` (`load`, `fetchone`) | malformed tape raises raw errors; encoded values while recording | #455 | |
| 30 | low | shape-fabric `_tsql.py` | zero-column table gives invalid SQL; long table name fails on the PK name | #456 | |
| 31 | low | shape-fabric `sqldb.py` (`write_many`) | missing table in `order` found after earlier tables were written | #458 | |
| 32 | low | shape-fabric `_storage.py` | local files written with mode 0600 | #459 | |
| 33 | low | shape-fabric `fabric_api.py` | token fetched once per client | #460 | |

Not filed (could not be confirmed offline): `COPY INTO` run when 0 rows were staged
(`warehouse.py`; the fake accepts it, and a live Warehouse was not available); Arrow `nullable=False`
not carried into `NOT NULL` (a design choice, not a defect).
