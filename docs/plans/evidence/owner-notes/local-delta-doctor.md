# Local Delta doctor receipt — CLI example 5

Captured on **2026-10-12 UTC**, repository `integrate/owner-notes` base `8d10aa0a768383a947fa77d9547b066f8cfbd9cc`, Shape **0.9.1**, Python **3.12.14**, Linux **6.18.44**, rust kernel. This is a real local repository run, not an Owner VM run or an expected-output block. No account, network service or credential is needed. The output records the installed package versions.

## Real generation and check

Run in a fresh directory:

```bash
shape generate retail --scale small --seed 7 --format delta -o delta_out
shape doctor --delta-table delta_out/customer
```

Generation output, exit **0**:

```text
Wrote 9 delta directories to delta_out: 21,750 rows in 9 tables (0.29s)
```

Doctor output, exit **0**:

```text
Shape 0.9.1
  python    3.12.14  (Linux-6.18.44-x86_64-with-glibc2.41)
  kernel    rust  (compiled)

Required
  OK      numpy         2.5.3
  OK      pyarrow       25.0.1

Optional
  OK      cryptography  50.0.2
  OK      yaml          6.0.3
  OK      pandas        3.0.6
  OK      scipy         1.18.1
  OK      deltalake     1.6.6
  OK      openpyxl      3.1.5
  OK      sklearn       1.9.1
  OK      tzdata        2026.3

Checks
  PASS  delta.limits  no deletion vectors or column mapping (delta-rs 1.6.6)

Result: OK
```

Both commands were replayed in a second fresh directory with the same seed. Their outputs match through the repository's `scripts/docs_transcripts.py` `comparable` function; only the declared elapsed-time field differs. No expected result was substituted for actual output.

## Resolution

Together with the existing [Owner-run Fabric, Kafka and Event Hubs doctor receipts](doctor-additional-environments.md), this supplies all four command shapes in `docs/CLI.md` example 5 and resolves that owner note. These are separate Linux-local and Windows/live-service runs, not a fabricated single-host transcript. The missing-path WARN in the older Owner receipt remains recorded and is not represented as a successful Delta check.

This implements the account-free portion of NEXT_VM_SESSION.md A4 locally; A5's three service checks already exist as Owner-run evidence. There is no need to use the VM again for this note.
