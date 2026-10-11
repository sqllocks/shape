# Live test playbook: Kafka (Confluent Cloud)

Shape 0.9.1 · plugin `sqllocks-shape-kafka` 0.9.1 (`confluent-kafka`; `[avro]`, `[protobuf]`
for registry formats) · checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Kafka.** Profiling a topic in bounded mode (`shape stream-profile
kafka://...`, windows, checkpoints) and emitting events (`shape emit --sink kafka://...`, one
message per event, key `<table>/<seq>`, `--event-format avro|protobuf|json-schema` through a
Confluent-compatible schema registry). **Confluent Cloud needs SASL_SSL**, which the stream
source takes through an options file but the emitter has no command-line option for (see
"Gaps"): the emit step works against a PLAINTEXT broker (the local emulator) and, on Confluent
Cloud, only through the Python API. No Kafka table sink for `shape generate --to`, and
`shape generate --from` does not read a stream profile, so the generation step profiles a
consumed dump of the topic instead.

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Cluster | Confluent Cloud **Basic** cluster (no hourly charge; pay per GB in, out and stored), one environment `shape-test`; or the local emulator `docker compose -f ci/emulators/docker-compose.yml up -d --wait kafka` (`confluentinc/cp-kafka`, PLAINTEXT on `localhost:9092`, free). |
| Topic | `shape_test`, 1 partition, 1-day retention, and (for the emit step) `shape_emit`, 1 partition. |
| Permissions | A Confluent API key for the cluster with `READ` and `WRITE` on the topics (a service account with ACLs, or your user key). For the Avro step, a Schema Registry API key with write. |
| Real topic to profile | Any topic whose messages are JSON objects. Confluent Cloud's Datagen Source connector (`orders` template, 1 message per second) fills `shape_test` with real-looking JSON in a minute. |
| Local tools | Confluent CLI (`confluent`) signed in, Python 3.11 to 3.14. |

## 2. Credentials

| Variable or file | Read by | Meaning |
|---|---|---|
| `kafka-options.json` (mode 600) with `{"config": {"security.protocol": "SASL_SSL", "sasl.mechanisms": "PLAIN", "sasl.username": "<API key>", "sasl.password": "<API secret>"}}` | `shape stream-profile --options-file` | the `confluent-kafka` consumer settings (`docs/plugins/streaming.md`, "Source options"); the file keeps the secret off the command line |
| `KAFKA_SERVERS`, `KAFKA_TOPIC`, `KAFKA_CONFIG` (the same JSON object as `config`) | `pytest -m live plugins/shape-kafka/tests/test_live.py` and the nightly `emit-live` job | the plugin's live test |
| `--sink-config kafka.schema_registry_url=https://...`, `kafka.schema_registry_username=<key>`, `kafka.schema_registry_password=env://SR_API_SECRET` | `shape emit --event-format avro` | the registry; the password is a reference, never a value |
| `CONFLUENT_CLOUD_API_KEY`, `CONFLUENT_CLOUD_API_SECRET` | the Confluent CLI | not read by Shape |

A `--sink-config kafka.password=<literal>` or any secret-looking literal is refused on the
command line (`env://`, `file://` and `kv://` references only).

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-kafka[avro]==0.9.1"
shape plugins list | grep -E 'shape.stream_sources:kafka|shape.emitters:kafka'
```

## 4. Test steps

Work in an empty directory. `KAFKA_SERVERS` is the bootstrap endpoint
(`pkc-....confluent.cloud:9092`, or `localhost:9092` for the emulator).

1. **Preflight.**
   ```bash
   shape doctor --broker "kafka://$KAFKA_SERVERS/shape_test" --no-auth-check
   ```
   Success: DNS, TCP and TLS checks pass; exit 0. (`--no-auth-check` because the doctor's
   sign-in step has no SASL settings; on the emulator leave it out.)

2. **Profile the real topic** (bounded: reads to the end the topic had when the read began):
   ```bash
   shape stream-profile "kafka://$KAFKA_SERVERS/shape_test" --options-file kafka-options.json \
       --start earliest --event-time ordertime --event-time-unit ms -o topic.json --json
   ```
   Success: exit 0 and one JSON line with `"events": N` (N > 0), `"undecodable": 0`,
   `"written": ["topic.json"]`; `topic.json` is a bounded-mode profile document
   (`"mode": "bounded"`). Use the field your messages carry as event time (`ordertime` for the
   Datagen orders template); without one, every event uses the broker timestamp.
   Windowed variant:
   ```bash
   shape stream-profile "kafka://$KAFKA_SERVERS/shape_test" --options-file kafka-options.json \
       --window tumbling --size 1m --windows topic-windows.jsonl --checkpoint topic.ckpt
   ```
   Success: `topic-windows.jsonl` has one line per closed window; a second run with the same
   checkpoint reads nothing new and says so.

3. **Generate from the topic.** `shape generate --from` reads `.shape` profiles, not the
   stream-profile document (it exits 2: "not a readable .shape archive"). Dump the topic to
   JSON lines with any consumer, then profile the dump:
   ```bash
   confluent kafka topic consume shape_test --from-beginning --timeout 30000 > topic.jsonl
   shape profile topic.jsonl --name orders -o orders.shape
   shape generate --from orders.shape --rows 10000 -o generated --format jsonl
   ```
   Success: `orders.shape` written (exit 0); `Wrote 1 jsonl files to generated: 10,000 rows`.
   (`shape stream-profile topic.jsonl -o events.json` on the dump must give the same column
   list as step 2.)

4. **Emit (write back).** Against the PLAINTEXT emulator:
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape emit retail --scale small --table order --max-events 5000 \
       --sink "kafka://localhost:9092/shape_emit" --json
   ```
   Success: exit 0 and a report with `"events": 5000`, `"complete": true`, `"retries": 0`;
   `shape stream-profile kafka://localhost:9092/shape_emit -o emitted.json` then reports
   `"events": 5000`. Against **Confluent Cloud the same command cannot be configured for
   SASL_SSL from the command line** (gap below); from Python:
   ```python
   from shape_kafka import KafkaEmitter   # emit(uri, batches, config={...SASL settings...})
   ```
   Record whether you ran the emulator or the API path.
   Avro through the registry (emulator or Cloud, same limitation):
   ```bash
   shape emit retail --scale small --table order --max-events 1000 --sink "kafka://localhost:9092/shape_emit_avro" \
       --event-format avro --sink-config kafka.schema_registry_url="$SR_URL" --yes --json
   ```
   Success: exit 0, `"events": 1000`; the registry shows subject `shape_emit_avro-value`.

5. **Contract check.**
   ```bash
   shape proposals propose orders.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o orders.contract.json
   shape check orders.shape orders.contract.json
   shape profile generated/orders.jsonl --name orders --capture full -o orders-generated.shape
   shape check orders-generated.shape orders.contract.json
   ```
   Success: the first `check` exits 0 with `{"passed": true, "violations": []}`; the second
   exits 0, or 1 with the violated rules listed (record them).

6. **Drift check.** Two bounded profiles of the same topic, then the dump against the generation:
   ```bash
   shape stream-profile "kafka://$KAFKA_SERVERS/shape_test" --options-file kafka-options.json --start earliest -o topic-again.json
   shape diff topic.json topic-again.json --fail-on-drift
   shape diff orders.shape orders-generated.shape --fail-on breaking --json drift.json
   ```
   Success: the first `diff` exits 0 (`[]`, no changes; `shape diff` reads the profile-engine
   JSON documents); the second exits 0 with `0 breaking`. If the Datagen connector kept
   producing between the two reads, `row_count_change` appears: that is real drift, not a
   failure; note it.

7. **Optional: the plugin's live test** (repository checkout):
   ```bash
   export KAFKA_SERVERS KAFKA_TOPIC=shape_emit KAFKA_CONFIG='{"security.protocol": "SASL_SSL", ...}'
   pytest -m live plugins/shape-kafka/tests/test_live.py -q
   ```
   Success: it passes (events are read back from the topic's end as it was before the test).
   Emulator: `pytest -m emulator plugins/shape-kafka/tests -q`.

## 5. Cleanup and cost

```bash
confluent kafka topic delete shape_test --force
confluent kafka topic delete shape_emit --force
confluent api-key delete <key id> --force
# pause or delete the Datagen connector: confluent connect cluster delete <id>
docker compose -f ci/emulators/docker-compose.yml down -v     # emulator
rm -f kafka-options.json topic.jsonl *.json *.jsonl *.shape *.ckpt; rm -rf generated
```

**Cost.** A Basic cluster has no base charge; a running Datagen connector bills per hour
(delete it) and the few megabytes of traffic and storage are cents. Confirm in the Confluent
console that no connector is running and the topics are gone; a Basic cluster with no topics
costs nothing.

## 6. Results

| Step | Command | Pass | Fail | Notes (events, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor --broker` | | | |
| 2 | `shape stream-profile kafka://` (global, windowed) | | | events: |
| 3 | dump + `shape profile` + `shape generate --from` | | | |
| 4a | `shape emit --sink kafka://` (emulator or API) | | | path used: |
| 4b | `shape emit --event-format avro` | | | subject: |
| 5 | `shape check` (real, generated) | | | rules: |
| 6 | `shape diff` (profiles, generated) | | | |
| 7 | `pytest -m live` / `-m emulator` | | | |

Tester, date, Shape version, cluster type: ______

## Gaps found while writing this playbook

- **Emitting to Confluent Cloud from the CLI.** The Kafka emitter's `--sink-config kafka.*`
  keys are only `schema_registry_url`, `subject_strategy`, `schema_registry_username` and
  `schema_registry_password` (`plugins/shape-kafka/src/shape_kafka/emitter.py`,
  `sink_config_keys`); the producer `config` option (SASL, TLS) is reachable only from Python.
  `shape emit` and `shape stream` have no `--options-file`. Belongs with W9-08/W9-09 or a small
  fix: add `config` (a `file://` reference to a JSON file) to `sink_config_keys`.
- `shape generate --from` does not accept a stream-profile document (`topic.json`); the topic
  must be dumped and profiled with `shape profile` first.
- `shape doctor --broker` has no way to pass SASL settings, so its sign-in step is skipped with
  `--no-auth-check` on Confluent Cloud.
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet (`README.md`).
