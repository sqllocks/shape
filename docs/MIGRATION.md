# Signed artifact migration

Status: experimental.

## Run the local examples

The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO` and installs
core and plugins from source. Start in an empty directory and run these commands in order.
The setup prepares a signed source artifact and an encrypted signing key. Generated signing
keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" MIGRATION
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for MIGRATION
    ```

## Signed artifacts and migration

A migrated artifact is a new file with a new manifest, so the old signature cannot be copied onto
it. `shape migrate` never touches the signed original (keep it: it is the evidence), writes the
migrated artifact as a new file, and writes a **signed receipt** that names both files by SHA-256
and records the source's signature:

[Run this example](#local-example-4).


`--verify` checks the source's signature first and stops if it fails; `--sign-key` signs the new
artifact and the receipt (`new.shape.receipt.json`). A signed source needs `--sign-key`, or
`--unsigned-receipt` to accept an unsigned receipt on purpose. Every signature, in an artifact and
in a receipt, carries its `algorithm`. See
[the state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md#6-signed-artifacts-and-migrations).

## Complete local run

The source in this example already uses the current format, so migration reports a no-op
and writes no artifact or receipt. A migration that changes the format writes the files described above.

<a id="local-example-4"></a>

### Example 1

<!-- example: 4 -->

```bash {.runnable-reference}
shape migrate old.shape new-signed.shape --verify old.pub --sign-key release.key
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"destination": null, "dry_run": false, "plan": {"kind": "artifact", "noop": true, "result_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "source_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "source_version": 2, "steps": [], "target_version": 2}, "receipt": null, "written": false}
    ```

## Related

[Signing](SIGNING.md) · [State and compatibility](specs/STATE_AND_COMPATIBILITY.md)
