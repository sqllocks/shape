# Azure Event Hubs (Basic tier, hub shape-emit, connection string in SHAPE_EVENTHUBS_CONNECTION_STRING) — Owner-run receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below is reproduced as supplied, including placeholders and abbreviated output. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
5. Azure Event Hubs (Basic tier, hub shape-emit, connection string in SHAPE_EVENTHUBS_CONNECTION_STRING):
   shape emit retail --scale small --table order --max-events 5000 --sink "eventhubs://<namespace>.servicebus.windows.net/shape-emit" --fresh --json
   -> {"events": 5000, "complete": true, "retries": 0, ..., "exit_code": 0}.
   shape stream-profile "eventhubs://<namespace>.servicebus.windows.net/shape-emit" --start earliest -o eventhubs-hub.json --json
   -> {"events": 5000, "undecodable": 0, "written": ["eventhubs-hub.json"], "exit_code": 0}.
```

## Note disposition

Evidence exists, not an exact match for a remaining owner note. No note is resolved by this receipt; see the [evidence index](remaining-live-receipts.md) for the command and service mismatches.
