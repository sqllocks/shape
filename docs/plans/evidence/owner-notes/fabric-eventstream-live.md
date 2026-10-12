# Fabric Eventstream (Custom endpoint source, connection string in SHAPE_EVENTSTREAM_CONNECTION_STRING) — Owner-run receipt

Owner-run evidence supplied by the Owner, dated **2026-10-11**: Shape **0.9.1** installed from PyPI in a clean Windows 11 VM, Python **3.12.10**, VM clock synced. The documentation editor did not execute these service runs. Additional client versions and exact capture times were not supplied and are not inferred.

The block below is reproduced as supplied, including placeholders and abbreviated output. Ellipses are omissions in the supplied receipt, not a complete JSON document or a fabricated transcript. Secrets were environment variables or references, never URI values; no secret values are recorded. Timings describe individual runs and are not benchmarks.

## Supplied receipt

```text
8. Fabric Eventstream (Custom endpoint source, connection string in SHAPE_EVENTSTREAM_CONNECTION_STRING):
   shape emit retail --scale small --table order --max-events 1000 --sink "eventstream://shape_es" --fresh --yes --json
   -> {"events": 1000, "complete": true, "stopped_by": "max-events", "exit_code": 0}. The Eventstream data preview showed the order rows (order_id, customer_id, store_id, shipping_address_id, promotion_id) flowing shape_in -> shape_es -> shape_eh.
```

## Note disposition

Evidence exists, not an exact match for a remaining owner note. No note is resolved by this receipt; see the [evidence index](remaining-live-receipts.md) for the command and service mismatches.
