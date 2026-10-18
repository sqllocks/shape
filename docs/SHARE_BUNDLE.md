# Safe-to-share bundle

Status: experimental.

A bundle is a zip of generated data, the run manifest when there is one, and `attestation.json`: a
signed statement that named checks passed on exactly this data. It gives the person you hand the
data to something to verify, instead of your word.

**What the attestation states:** the listed checks, with their parameters, passed on the generated
tables, when compared with the source tables they were made from; the data in the bundle has the
`dataset_id` the attestation names; and, when signed, the key that signed it.

**What it does not state:** that the data is private. It is evidence that these checks passed, not a
privacy guarantee. It says nothing about attacks the checks do not model (linkage with other data,
inference from the whole distribution, membership), about columns not classified CONFIDENTIAL or
above, or about tables with no source table to compare with. A signature says who signed it, not
that the signer is trustworthy.

## Commands

<!-- example: 0 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape share-bundle create DATA_DIR --source SOURCE_DIR --classifications CLASSES.json \
    -o BUNDLE.zip [--key PRIVATE_KEY] [--top-k 20]
shape share-bundle verify BUNDLE.zip [--public-key KEY]
```


`DATA_DIR` and `SOURCE_DIR` hold one `NAME.csv`, `NAME.parquet` or `NAME.jsonl` per table, as for
`shape verify --source`; a table is compared with the source table of the same name.
`CLASSES.json` maps `"table.column"` to a level (`PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `SECRET`,
`TOP_SECRET`; `PII` and `SENSITIVE` stand for `CONFIDENTIAL`), bare or under a `"classifications"` key.
There is no option to write a bundle whose checks failed.

### The checks

1. `memorization`: the memorization gate of `shape verify` ([VERIFY.md](VERIFY.md)) at
   `CONFIDENTIAL`: no generated row reproduces a source row on the columns classified `CONFIDENTIAL`
   or above.
2. `top_values`: for each column of each table classified `CONFIDENTIAL` or above, none of the
   source's `--top-k` (default 20) most frequent values, ties broken by value, appears in the
   generated column. Nulls are ignored.

If a check fails, `create` exits 1 and writes no bundle (an earlier file at the output path is left
as it was). It prints table and column names and counts; it never prints, and never writes into the
bundle or the attestation, a value from the source. `create` exits 2, with no bundle, when the
classifications mark no column of a generated table `CONFIDENTIAL` or above (the checks would test
nothing), when no generated table has a source table, for an unknown level, a bad `--top-k`, two run
manifests in `DATA_DIR`, or when `--key` is given without the `[sign]` extra.

### The bundle

```
BUNDLE.zip
  attestation.json
  manifest.json          the run manifest (`*manifest.json` in DATA_DIR), only when present
  data/NAME.parquet      every table file of DATA_DIR, as it is
```

The zip is reproducible: sorted members, fixed timestamps.

### `attestation.json`

`shape-share-attestation`, version 1. Counts and names only:

```json
{
  "format": "shape-share-attestation",
  "version": 1,
  "dataset_id": "sha256:...",
  "shape_version": "0.9.0",
  "tables": {"people": {"rows": 120}},
  "manifest_sha256": null,
  "checks": [
    {"name": "memorization", "parameters": {"fail_at": "CONFIDENTIAL"}, "passed": true,
     "counts": {"tables_compared": 1, "tables_not_compared": 0,
                "tables": {"people": {"rows": 120, "source_rows": 120, "reproduced_rows": 0,
                                       "restricted_columns": 2}}}},
    {"name": "top_values", "parameters": {"top_k": 20, "min_level": "CONFIDENTIAL"}, "passed": true,
     "counts": {"columns": [{"table": "people", "column": "name", "values_checked": 20, "matches": 0}]}}
  ],
  "key_id": null,
  "signature": null
}
```

`dataset_id` is [`shape.repro.dataset_id`](REPRODUCIBILITY.md) of the bundled tables as `verify`
reads them back (for CSV and JSONL, the types are those the reader infers). `manifest_sha256` is the
SHA-256 of `manifest.json`, or `null`. With `--key` the attestation is signed with Ed25519 (extra
`[sign]`) over its canonical form with `signature` set to `null`, under its own domain tag; keys are
those of [SIGNING.md](SIGNING.md). A reader refuses a version newer than it knows.

## Verifying

`verify` reads the zip without extracting anything to a path of its own choosing: it checks every
member name first and accepts only `attestation.json`, `manifest.json` and `data/<file>.csv|
.parquet|.jsonl`; absolute paths, `..`, backslashes, drive letters, directory entries, symbolic
links, duplicates and any other member make it exit 2 before anything is read. It then recomputes
`dataset_id` from the bundled data, compares the manifest's hash, checks that the `memorization`
and `top_values` checks are present and every check passed, and with `--public-key` checks the
signature (a signed bundle without a key is reported as "signature not checked"; an unsigned one
fails when a key is given).

A bundle comes from someone else, so every member is bounded before it is read (#684). The limits
are checked against the sizes the zip declares before anything is extracted, and again against the
bytes actually inflated while reading, since a zip's headers can lie; a bundle over a limit is
malformed (exit 2), the message names the member and the limit, and nothing extracted is left
behind.

| limit | value | why |
|---|---|---|
| members | 10,000 | the `.shape` container reader's member limit; a bundle holds one file per table |
| `attestation.json`, `manifest.json` | 8 MiB each | real ones are a few KiB |
| one data file | 2 GiB | generous for generated tables; `verify` loads every table into memory anyway |
| all data files | 8 GiB | the same reason |
| expansion | refused past an expansion factor of 100 once larger than 16 MiB, per file and in total | the Excel reader's zip-bomb guard |

| exit | meaning |
|---|---|
| 0 | valid |
| 1 | tampered (a data file changed, added or removed; the manifest or the signed attestation changed; a signature by another key) or a recorded check did not pass |
| 2 | malformed: not a zip, no or invalid `attestation.json`, wrong format, a newer `version`, a path outside the layout, a member over a size limit, unreadable data |

An unsigned attestation is only as trustworthy as the channel it came through: someone who can edit
the zip can edit the attestation and recompute the id. Sign it.
