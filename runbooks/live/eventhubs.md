# Live test playbook: Azure Event Hubs

Shape 0.9.1 · plugin `sqllocks-shape-eventhubs` 0.9.1 (`azure-eventhub`; `[entra]` for Microsoft
Entra sign-in) · checked against `release/0.9.1` on 2026-10-11.

**What 0.9.1 supports on Event Hubs.** Profiling a hub in bounded mode (`shape stream-profile
eventhubs://...`, explicit sequence numbers per partition, windows, checkpoints) and emitting
events (`shape emit --sink eventhubs://...`, service batches by size, idempotency key in the
property `shape_key`). No Event Hubs table sink for `shape generate --to`; `shape generate
--from` does not read a stream profile (the generation step profiles a consumed dump).

## 1. Prerequisites

| Item | Smallest thing that works |
|---|---|
| Namespace | Event Hubs **Basic** tier, 1 throughput unit, in a resource group `shape-test`; or the local emulator `docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs` (`eventhubs-emulator` with an Azurite sidecar, amd64 only, free). |
| Hub | `shape-test`, 2 partitions, 1-day retention (Basic's maximum); for the emit step `shape-emit`, 2 partitions. Basic allows one consumer group (`$Default`). |
| Permissions | A shared access policy on the namespace or hub with **Send** and **Listen** (the connection string), or your Entra user with the role **Azure Event Hubs Data Owner** on the namespace (`[entra]` extra, `az login`). |
| Real hub to profile | Any hub carrying JSON objects: fill `shape-test` with step 4's emit first, or point at an existing telemetry hub with Listen rights. |
| Local tools | Azure CLI signed in (`az login`), Python 3.11 to 3.14. |

```bash
az group create -n shape-test -l westeurope
az eventhubs namespace create -g shape-test -n "$EVENTHUBS_NAMESPACE_NAME" --sku Basic --capacity 1
az eventhubs eventhub create -g shape-test --namespace-name "$EVENTHUBS_NAMESPACE_NAME" -n shape-test --partition-count 2 --retention-time 24
az eventhubs eventhub create -g shape-test --namespace-name "$EVENTHUBS_NAMESPACE_NAME" -n shape-emit --partition-count 2 --retention-time 24
```

## 2. Credentials

| Variable or option | Read by | Meaning |
|---|---|---|
| `SHAPE_EVENTHUBS_CONNECTION_STRING` | the `eventhubs://` stream source and emitter | the namespace or hub connection string (`Endpoint=sb://...;SharedAccessKeyName=...;SharedAccessKey=...`); the hub name comes from the URI |
| `--option connection_string=...` / `--options-file` | `shape stream-profile` | the same, from a file (mode 600) instead of the environment |
| none, with `[entra]` installed | source and emitter | `DefaultAzureCredential` (managed identity, service principal from `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, or the Azure CLI) |
| `EVENTHUBS_CONNECTION_STRING`, `EVENTHUBS_HUB`, `EVENTHUBS_GROUP` (optional) | `pytest -m live plugins/shape-eventhubs/tests/test_live.py` and the nightly `emit-live` job | the plugin's live test (a missing variable fails the test by name) |

Read the connection string into the shell without echoing it:
`export SHAPE_EVENTHUBS_CONNECTION_STRING="$(az eventhubs namespace authorization-rule keys list -g shape-test --namespace-name "$EVENTHUBS_NAMESPACE_NAME" -n RootManageSharedAccessKey --query primaryConnectionString -o tsv)"`.
Never write it to a file you commit; a `--sink-config` literal that looks like a key is refused.

## 3. Install

Plugins are not on PyPI at 0.9.1: install from the 0.9.1 wheel set in `$SHAPE_WHEELS`
(`README.md`, "Install"):

```bash
pip install --find-links "$SHAPE_WHEELS" "sqllocks-shape[domains]==0.9.1" \
    "sqllocks-shape-eventhubs[entra]==0.9.1"
shape plugins list | grep -E 'shape.stream_sources:eventhubs|shape.emitters:eventhubs'
```

## 4. Test steps

Work in an empty directory. `EVENTHUBS_NAMESPACE` is `<name>.servicebus.windows.net`
(the emulator: `localhost` with its documented development connection string).

1. **Preflight.**
   ```bash
   shape doctor --broker "eventhubs://$EVENTHUBS_NAMESPACE/shape-test"
   ```
   Success: DNS, TCP, TLS and sign-in pass; exit 0.

2. **Emit (fill the hub, and the write-back test).**
   ```bash
   export SHAPE_CONFIRM_REMOTE=1
   shape emit retail --scale small --table order --max-events 5000 \
       --sink "eventhubs://$EVENTHUBS_NAMESPACE/shape-test" --json
   ```
   Success: exit 0 and a report with `"events": 5000`, `"complete": true`; the portal's hub
   metrics show 5,000 incoming messages. A rerun of the identical command says
   `already_complete` (the checkpoint is final); add `--fresh` to send again.

3. **Profile the real hub** (bounded, from the earliest sequence number of each partition):
   ```bash
   shape stream-profile "eventhubs://$EVENTHUBS_NAMESPACE/shape-test" --start earliest \
       -o hub.json --json
   ```
   Success: exit 0 and one JSON line with `"events": 5000`, `"undecodable": 0`,
   `"written": ["hub.json"]` (the events carry `_shape_event_time`, so no `--event-time` is
   needed). Windowed, resumable variant:
   ```bash
   shape stream-profile "eventhubs://$EVENTHUBS_NAMESPACE/shape-test" --start earliest \
       --window tumbling --size 1h --windows hub-windows.jsonl --checkpoint hub.ckpt --max-partition-skew 1d
   ```
   Success: `hub-windows.jsonl` has one line per closed window; the same command again reads
   nothing new.

4. **Generate from the hub.** `shape generate --from` reads `.shape` profiles, not the
   stream-profile document. Dump the hub to JSON lines first (any consumer that writes one
   JSON object per line; for a hub Shape filled, the emitted events are already in
   `shape emit ... --sink file -o orders.jsonl` form), then:
   ```bash
   shape emit retail --scale small --table order --max-events 5000 --sink file -o orders.jsonl
   shape profile orders.jsonl --name order -o order.shape
   shape generate --from order.shape --rows 10000 -o generated --format jsonl
   ```
   Success: exit 0 twice and `Wrote 1 jsonl files to generated: 10,000 rows in 1 tables`. Then
   emit the generated rows to the second hub through the same transport:
   ```bash
   shape emit retail --scale small --table order --max-events 10000 \
       --sink "eventhubs://$EVENTHUBS_NAMESPACE/shape-emit" --fresh --json
   ```
   Success: `"events": 10000`, `"complete": true`.

5. **Contract check.**
   ```bash
   shape proposals propose order.shape -d decisions.json --kinds rule --auto-accept 0.5
   shape proposals contract -d decisions.json -o order.contract.json
   shape check order.shape order.contract.json
   shape profile generated/order.jsonl --name order --capture full -o order-generated.shape
   shape check order-generated.shape order.contract.json
   ```
   Success: the first `check` exits 0 with `{"passed": true, "violations": []}`; the second
   exits 0, or 1 with the violated rules listed (record them).

6. **Drift check.**
   ```bash
   shape stream-profile "eventhubs://$EVENTHUBS_NAMESPACE/shape-test" --start earliest -o hub-again.json
   shape diff hub.json hub-again.json --fail-on-drift
   shape stream-profile "eventhubs://$EVENTHUBS_NAMESPACE/shape-emit" --start earliest -o emitted.json
   shape diff hub.json emitted.json --fail-on breaking --json drift.json
   ```
   Success: the first `diff` exits 0 (`[]`); the second exits 0 with `0 breaking`
   (`row_count_change` from 5,000 to 10,000 events is additive, not breaking).

7. **Optional: the plugin's live and emulator tests** (repository checkout):
   ```bash
   export EVENTHUBS_CONNECTION_STRING="$SHAPE_EVENTHUBS_CONNECTION_STRING" EVENTHUBS_HUB=shape-emit
   pytest -m live plugins/shape-eventhubs/tests/test_live.py -q
   docker compose -f ci/emulators/docker-compose.yml up -d --wait azurite eventhubs && pytest -m emulator plugins/shape-eventhubs/tests -q
   ```
   Success: both pass.

## 5. Cleanup and cost

```bash
az group delete -n shape-test --yes --no-wait
docker compose -f ci/emulators/docker-compose.yml down -v
unset SHAPE_EVENTHUBS_CONNECTION_STRING EVENTHUBS_CONNECTION_STRING
rm -f *.json *.jsonl *.shape *.ckpt; rm -rf generated
```

**Cost.** Basic tier bills per throughput unit per hour (about one and a half US cents per hour
at list price, plus a negligible per-million-events charge); an afternoon is under a dollar.
Deleting the resource group stops the charge; `az group exists -n shape-test` must print
`false` when the deletion finishes.

## 6. Results

| Step | Command | Pass | Fail | Notes (events, exit code, violated rules, drifted columns) |
|---|---|---|---|---|
| 1 | `shape doctor --broker eventhubs://` | | | |
| 2 | `shape emit --sink eventhubs://` (5,000) | | | |
| 3 | `shape stream-profile eventhubs://` (global, windowed) | | | events: |
| 4a | dump + `shape profile` + `shape generate --from` | | | |
| 4b | `shape emit` generated rows (10,000) | | | |
| 5 | `shape check` (real, generated) | | | rules: |
| 6 | `shape diff` (same hub, emitted) | | | bump: |
| 7 | `pytest -m live` / `-m emulator` | | | |

Tester, date, Shape version, tier: ______

## Gaps found while writing this playbook

- `shape generate --from` does not accept a stream-profile document; a hub must be dumped and
  profiled with `shape profile` first (no Shape command dumps a hub to a file).
- No Event Hubs table sink for `shape generate --to` (emit is the write path).
- Install: the plugin is not on PyPI and `v0.9.1` has no tag or release yet (`README.md`).
