# Profile registry and profile files

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" PROFILE_REGISTRY
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for PROFILE_REGISTRY
    ```


Two groups of commands manage profiles. Both read and write Shape's own formats only: a `.shape`
profile artifact, and the `shape-profile` JSON that `export` writes.

## Profile files

[Run this example](#local-example-0).


`import` accepts only `shape-profile` JSON; any other JSON file is refused with exit 2.
`list` prints what it skipped (a file that is not a profile) on stderr instead of hiding it.

## The profile registry

A directory of named, tagged profiles, one table profile per file (`name.shape`, or `name.safe.json`
for the safe form below):

```
<root>/<system>/<table>/<name>.shape      identity: system/table/name
<root>/_index.json                        rebuilt from the files by `reindex`
<root>/_layout.json                       the layout version (written when a registry is opened)
```

The root is `--root DIR`, else `$SHAPE_PROFILE_REGISTRY`, else `~/.shape/profiles`. The
description and tags are kept inside each file, so the index can always be rebuilt.

<!-- example: 2 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape profile registry save orders.csv --system crm --name 2026Q2 --tags prod,daily
shape profile registry save orders.shape --system crm --name 2026Q2 --overwrite
shape profile registry list [--system S] [--table T] [--tag X]... [--query TEXT] [--json]
shape profile registry tag crm/orders/2026Q2 reviewed        # --remove to drop tags
shape profile registry diff crm/orders/2026Q2 crm/orders/2026Q3   # --fail-on-diff for CI
shape profile registry validate [crm/orders/2026Q2] [--data new.csv --tolerance 0.05]
shape profile registry reindex
shape profile registry delete crm/orders/2026Q2
```


`save` takes data (it is profiled) or a `.shape` profile, and stores one entry per table.
`validate` checks the files and the index; with `--data` it also compares the data's profile with
the stored one (columns added or removed, type changes, null-rate drift) and exits 1 on a
difference.

Safeguards: every identity part is a plain name (letters, digits, `.`, `_`, `-`), so nothing can
leave the root; files and the index are written atomically; `save` refuses to replace an existing
profile without `--overwrite`; `reindex` lists every file it could not read.

### Real values, and the safe form

By default a stored profile is a safe capture, as written by `shape profile -o`
(`docs/PRIVACY_MODEL.md`): a sensitive column keeps statistics and formats only and a category is
kept only when every released category has at least `k` rows (`--k N`, `--column-k COLUMN=N`,
`--classify COLUMN=LEVEL`). `save --capture full` keeps **real values from the data** (up to 500
per column with their counts, and each column's minimum and maximum), says so in the artifact and
on stderr (`shape: warning: --capture full keeps real values in ROOT; do not commit or share it`);
such a store is a private catalog, and the default root is under your home folder. `list` shows
`safe capture` or `full` in its last column.

For the stricter share-safe profile JSON, save with `--safe`:

<!-- example: 3 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape profile registry save orders.csv --system crm --name 2026Q2 --safe [--k N] [--sensitive]
```


A safe entry is the output of `shape profile safe` (one table, no field that can hold a raw value
or a value list), stored as `<system>/<table>/<name>.safe.json`. It is checked with the leak
scanner when it is saved and again by `registry validate`, and
`shape profile validate --safe` accepts the file. `list`, `tag`, `diff`, `validate --data`,
`reindex` and `delete` work on it; the column fields that `diff` compares are the safe profile's
(`mean`, `quantiles`, `categorical_weights`, ...). An identity has one form at a time: saving the
other form needs `--overwrite` and replaces it. Relationships between tables are not kept in a
safe entry. A description is free text and is scanned too: one that looks like personal data is
refused.

`shape registry` is a different store: the content-addressed registry of artifacts
(`docs/REGISTRY.md`), which refuses a raw profile unless told otherwise. The two stores are
separate and are never merged.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape profile export orders.shape -o orders.json    # portable JSON, round-trips exactly
shape profile import orders.json -o orders.shape    # --name NAME renames it
shape profile list DIR                              # the .shape profiles in a directory (--json)
shape profile validate orders.shape                 # well formed? tampered? (exit 0 / 1)
shape profile validate --safe orders.json           # the leak scanner (see PRIVACY_MODEL.md)
```

??? info "Output (exit 1)"

    ```text {.expected}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {
      "exported": "orders.json",
      "name": "orders",
      "tables": [
        "orders"
      ]
    }
    {
      "written": "orders.shape",
      "name": "orders",
      "shape_content_id": "a2946f30e608293e1d58ee69f447666c9dbddd05fd07097913e1c77d45e64811"
    }
    shape: error: not a directory: DIR
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    VALID: orders.shape
    LEAK: 474 finding(s) in orders.json:
      [not-safe-profile] $: artifact lacks safe-profile markers (['redaction_manifest', 'schema_version']); foreign or full-fidelity JSON is flagged, only proven-clean artifacts pass
      [full-capture] $.capture.mode: profile was captured full (--capture full): it keeps real values from the data and is not for sharing; re-profile with the default (--capture safe)
      [format-version] $: not a safe profile: its format is 'shape-profile', expected 'shape-safe-profile'
      [raw-string-list] $.profile.columns.order_id.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['1', '2', '3']
      [raw-string-list] $.profile.columns.customer_id.value_counts_ext_order: list under 'value_counts_ext_order' carries 20 raw strings (more than 2): a value dump. Sample: ['2', '3', '4']
      [pii-regex] $.profile.columns.customer_email.min_value[1]: value matches the email pattern: 'person0@example.test'
      [pii-regex] $.profile.columns.customer_email.min_value[1]: value matches the email pattern: 'person0@example.test'
      [pii-regex] $.profile.columns.customer_email.max_value[1]: value matches the email pattern: 'person9@example.test'
      [pii-regex] $.profile.columns.customer_email.max_value[1]: value matches the email pattern: 'person9@example.test'
      [raw-string-list] $.profile.columns.customer_email.value_counts_ext_order: list under 'value_counts_ext_order' carries 20 raw strings (more than 2): a value dump. Sample: ['person1@example.test', 'person2@example.test', 'person3@example.test']
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[0]: value matches the email pattern: 'person1@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[0]: value matches the email pattern: 'person1@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[1]: value matches the email pattern: 'person2@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[1]: value matches the email pattern: 'person2@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[2]: value matches the email pattern: 'person3@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[2]: value matches the email pattern: 'person3@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[3]: value matches the email pattern: 'person4@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[3]: value matches the email pattern: 'person4@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[4]: value matches the email pattern: 'person5@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[4]: value matches the email pattern: 'person5@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[5]: value matches the email pattern: 'person6@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[5]: value matches the email pattern: 'person6@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[6]: value matches the email pattern: 'person7@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[6]: value matches the email pattern: 'person7@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[7]: value matches the email pattern: 'person8@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[7]: value matches the email pattern: 'person8@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[8]: value matches the email pattern: 'person9@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[8]: value matches the email pattern: 'person9@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[9]: value matches the email pattern: 'person10@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[9]: value matches the email pattern: 'person10@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[10]: value matches the email pattern: 'person11@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[10]: value matches the email pattern: 'person11@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[11]: value matches the email pattern: 'person12@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[11]: value matches the email pattern: 'person12@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[12]: value matches the email pattern: 'person13@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[12]: value matches the email pattern: 'person13@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[13]: value matches the email pattern: 'person14@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[13]: value matches the email pattern: 'person14@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[14]: value matches the email pattern: 'person15@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[14]: value matches the email pattern: 'person15@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[15]: value matches the email pattern: 'person16@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[15]: value matches the email pattern: 'person16@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[16]: value matches the email pattern: 'person17@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[16]: value matches the email pattern: 'person17@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[17]: value matches the email pattern: 'person18@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[17]: value matches the email pattern: 'person18@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[18]: value matches the email pattern: 'person19@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[18]: value matches the email pattern: 'person19@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[19]: value matches the email pattern: 'person0@example.test'
      [pii-regex] $.profile.columns.customer_email.value_counts_ext_order[19]: value matches the email pattern: 'person0@example.test'
      [raw-string-list] $.profile.columns.amount.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['10.571', '11.142', '11.713']
      [raw-string-list] $.profile.columns.order_total.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['10.571', '11.142', '11.713']
      [raw-string-list] $.profile.columns.placed_at.value_counts_ext_order: list under 'value_counts_ext_order' carries 28 raw strings (more than 2): a value dump. Sample: ['2026-09-02T12:00:00', '2026-09-03T12:00:00', '2026-09-04T12:00:00']
      [raw-string-list] $.profile.columns.shipped_at.value_counts_ext_order: list under 'value_counts_ext_order' carries 28 raw strings (more than 2): a value dump. Sample: ['2026-09-02T13:00:00', '2026-09-03T13:00:00', '2026-09-04T13:00:00']
      [raw-string-list] $.profile.columns.order_date.value_counts_ext_order: list under 'value_counts_ext_order' carries 28 raw strings (more than 2): a value dump. Sample: ['2026-09-02T12:00:00', '2026-09-03T12:00:00', '2026-09-04T12:00:00']
      [pii-regex] $.profile.columns.iban.min_value[1]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.iban.min_value[1]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.iban.max_value[1]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.iban.max_value[1]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.iban.value_counts_ext_order[0]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.iban.value_counts_ext_order[0]: value matches the iban pattern: 'GB82WEST12345698765432'
      [pii-regex] $.profile.columns.token.min_value[1]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.token.min_value[1]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.token.max_value[1]: value matches the ssn pattern: '222-11-0100'
      [pii-regex] $.profile.columns.token.max_value[1]: value matches the ssn pattern: '222-11-0100'
      [raw-string-list] $.profile.columns.token.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['222-11-0001', '222-11-0002', '222-11-0003']
      [pii-regex] $.profile.columns.token.value_counts_ext_order[0]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[0]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[1]: value matches the ssn pattern: '222-11-0002'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[1]: value matches the ssn pattern: '222-11-0002'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[2]: value matches the ssn pattern: '222-11-0003'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[2]: value matches the ssn pattern: '222-11-0003'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[3]: value matches the ssn pattern: '222-11-0004'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[3]: value matches the ssn pattern: '222-11-0004'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[4]: value matches the ssn pattern: '222-11-0005'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[4]: value matches the ssn pattern: '222-11-0005'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[5]: value matches the ssn pattern: '222-11-0006'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[5]: value matches the ssn pattern: '222-11-0006'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[6]: value matches the ssn pattern: '222-11-0007'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[6]: value matches the ssn pattern: '222-11-0007'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[7]: value matches the ssn pattern: '222-11-0008'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[7]: value matches the ssn pattern: '222-11-0008'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[8]: value matches the ssn pattern: '222-11-0009'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[8]: value matches the ssn pattern: '222-11-0009'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[9]: value matches the ssn pattern: '222-11-0010'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[9]: value matches the ssn pattern: '222-11-0010'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[10]: value matches the ssn pattern: '222-11-0011'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[10]: value matches the ssn pattern: '222-11-0011'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[11]: value matches the ssn pattern: '222-11-0012'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[11]: value matches the ssn pattern: '222-11-0012'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[12]: value matches the ssn pattern: '222-11-0013'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[12]: value matches the ssn pattern: '222-11-0013'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[13]: value matches the ssn pattern: '222-11-0014'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[13]: value matches the ssn pattern: '222-11-0014'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[14]: value matches the ssn pattern: '222-11-0015'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[14]: value matches the ssn pattern: '222-11-0015'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[15]: value matches the ssn pattern: '222-11-0016'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[15]: value matches the ssn pattern: '222-11-0016'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[16]: value matches the ssn pattern: '222-11-0017'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[16]: value matches the ssn pattern: '222-11-0017'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[17]: value matches the ssn pattern: '222-11-0018'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[17]: value matches the ssn pattern: '222-11-0018'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[18]: value matches the ssn pattern: '222-11-0019'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[18]: value matches the ssn pattern: '222-11-0019'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[19]: value matches the ssn pattern: '222-11-0020'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[19]: value matches the ssn pattern: '222-11-0020'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[20]: value matches the ssn pattern: '222-11-0021'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[20]: value matches the ssn pattern: '222-11-0021'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[21]: value matches the ssn pattern: '222-11-0022'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[21]: value matches the ssn pattern: '222-11-0022'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[22]: value matches the ssn pattern: '222-11-0023'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[22]: value matches the ssn pattern: '222-11-0023'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[23]: value matches the ssn pattern: '222-11-0024'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[23]: value matches the ssn pattern: '222-11-0024'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[24]: value matches the ssn pattern: '222-11-0025'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[24]: value matches the ssn pattern: '222-11-0025'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[25]: value matches the ssn pattern: '222-11-0026'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[25]: value matches the ssn pattern: '222-11-0026'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[26]: value matches the ssn pattern: '222-11-0027'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[26]: value matches the ssn pattern: '222-11-0027'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[27]: value matches the ssn pattern: '222-11-0028'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[27]: value matches the ssn pattern: '222-11-0028'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[28]: value matches the ssn pattern: '222-11-0029'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[28]: value matches the ssn pattern: '222-11-0029'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[29]: value matches the ssn pattern: '222-11-0030'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[29]: value matches the ssn pattern: '222-11-0030'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[30]: value matches the ssn pattern: '222-11-0031'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[30]: value matches the ssn pattern: '222-11-0031'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[31]: value matches the ssn pattern: '222-11-0032'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[31]: value matches the ssn pattern: '222-11-0032'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[32]: value matches the ssn pattern: '222-11-0033'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[32]: value matches the ssn pattern: '222-11-0033'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[33]: value matches the ssn pattern: '222-11-0034'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[33]: value matches the ssn pattern: '222-11-0034'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[34]: value matches the ssn pattern: '222-11-0035'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[34]: value matches the ssn pattern: '222-11-0035'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[35]: value matches the ssn pattern: '222-11-0036'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[35]: value matches the ssn pattern: '222-11-0036'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[36]: value matches the ssn pattern: '222-11-0037'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[36]: value matches the ssn pattern: '222-11-0037'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[37]: value matches the ssn pattern: '222-11-0038'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[37]: value matches the ssn pattern: '222-11-0038'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[38]: value matches the ssn pattern: '222-11-0039'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[38]: value matches the ssn pattern: '222-11-0039'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[39]: value matches the ssn pattern: '222-11-0040'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[39]: value matches the ssn pattern: '222-11-0040'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[40]: value matches the ssn pattern: '222-11-0041'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[40]: value matches the ssn pattern: '222-11-0041'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[41]: value matches the ssn pattern: '222-11-0042'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[41]: value matches the ssn pattern: '222-11-0042'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[42]: value matches the ssn pattern: '222-11-0043'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[42]: value matches the ssn pattern: '222-11-0043'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[43]: value matches the ssn pattern: '222-11-0044'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[43]: value matches the ssn pattern: '222-11-0044'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[44]: value matches the ssn pattern: '222-11-0045'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[44]: value matches the ssn pattern: '222-11-0045'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[45]: value matches the ssn pattern: '222-11-0046'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[45]: value matches the ssn pattern: '222-11-0046'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[46]: value matches the ssn pattern: '222-11-0047'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[46]: value matches the ssn pattern: '222-11-0047'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[47]: value matches the ssn pattern: '222-11-0048'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[47]: value matches the ssn pattern: '222-11-0048'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[48]: value matches the ssn pattern: '222-11-0049'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[48]: value matches the ssn pattern: '222-11-0049'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[49]: value matches the ssn pattern: '222-11-0050'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[49]: value matches the ssn pattern: '222-11-0050'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[50]: value matches the ssn pattern: '222-11-0051'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[50]: value matches the ssn pattern: '222-11-0051'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[51]: value matches the ssn pattern: '222-11-0052'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[51]: value matches the ssn pattern: '222-11-0052'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[52]: value matches the ssn pattern: '222-11-0053'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[52]: value matches the ssn pattern: '222-11-0053'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[53]: value matches the ssn pattern: '222-11-0054'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[53]: value matches the ssn pattern: '222-11-0054'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[54]: value matches the ssn pattern: '222-11-0055'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[54]: value matches the ssn pattern: '222-11-0055'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[55]: value matches the ssn pattern: '222-11-0056'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[55]: value matches the ssn pattern: '222-11-0056'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[56]: value matches the ssn pattern: '222-11-0057'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[56]: value matches the ssn pattern: '222-11-0057'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[57]: value matches the ssn pattern: '222-11-0058'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[57]: value matches the ssn pattern: '222-11-0058'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[58]: value matches the ssn pattern: '222-11-0059'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[58]: value matches the ssn pattern: '222-11-0059'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[59]: value matches the ssn pattern: '222-11-0060'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[59]: value matches the ssn pattern: '222-11-0060'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[60]: value matches the ssn pattern: '222-11-0061'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[60]: value matches the ssn pattern: '222-11-0061'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[61]: value matches the ssn pattern: '222-11-0062'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[61]: value matches the ssn pattern: '222-11-0062'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[62]: value matches the ssn pattern: '222-11-0063'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[62]: value matches the ssn pattern: '222-11-0063'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[63]: value matches the ssn pattern: '222-11-0064'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[63]: value matches the ssn pattern: '222-11-0064'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[64]: value matches the ssn pattern: '222-11-0065'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[64]: value matches the ssn pattern: '222-11-0065'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[65]: value matches the ssn pattern: '222-11-0066'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[65]: value matches the ssn pattern: '222-11-0066'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[66]: value matches the ssn pattern: '222-11-0067'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[66]: value matches the ssn pattern: '222-11-0067'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[67]: value matches the ssn pattern: '222-11-0068'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[67]: value matches the ssn pattern: '222-11-0068'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[68]: value matches the ssn pattern: '222-11-0069'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[68]: value matches the ssn pattern: '222-11-0069'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[69]: value matches the ssn pattern: '222-11-0070'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[69]: value matches the ssn pattern: '222-11-0070'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[70]: value matches the ssn pattern: '222-11-0071'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[70]: value matches the ssn pattern: '222-11-0071'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[71]: value matches the ssn pattern: '222-11-0072'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[71]: value matches the ssn pattern: '222-11-0072'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[72]: value matches the ssn pattern: '222-11-0073'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[72]: value matches the ssn pattern: '222-11-0073'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[73]: value matches the ssn pattern: '222-11-0074'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[73]: value matches the ssn pattern: '222-11-0074'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[74]: value matches the ssn pattern: '222-11-0075'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[74]: value matches the ssn pattern: '222-11-0075'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[75]: value matches the ssn pattern: '222-11-0076'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[75]: value matches the ssn pattern: '222-11-0076'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[76]: value matches the ssn pattern: '222-11-0077'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[76]: value matches the ssn pattern: '222-11-0077'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[77]: value matches the ssn pattern: '222-11-0078'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[77]: value matches the ssn pattern: '222-11-0078'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[78]: value matches the ssn pattern: '222-11-0079'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[78]: value matches the ssn pattern: '222-11-0079'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[79]: value matches the ssn pattern: '222-11-0080'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[79]: value matches the ssn pattern: '222-11-0080'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[80]: value matches the ssn pattern: '222-11-0081'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[80]: value matches the ssn pattern: '222-11-0081'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[81]: value matches the ssn pattern: '222-11-0082'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[81]: value matches the ssn pattern: '222-11-0082'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[82]: value matches the ssn pattern: '222-11-0083'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[82]: value matches the ssn pattern: '222-11-0083'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[83]: value matches the ssn pattern: '222-11-0084'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[83]: value matches the ssn pattern: '222-11-0084'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[84]: value matches the ssn pattern: '222-11-0085'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[84]: value matches the ssn pattern: '222-11-0085'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[85]: value matches the ssn pattern: '222-11-0086'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[85]: value matches the ssn pattern: '222-11-0086'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[86]: value matches the ssn pattern: '222-11-0087'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[86]: value matches the ssn pattern: '222-11-0087'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[87]: value matches the ssn pattern: '222-11-0088'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[87]: value matches the ssn pattern: '222-11-0088'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[88]: value matches the ssn pattern: '222-11-0089'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[88]: value matches the ssn pattern: '222-11-0089'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[89]: value matches the ssn pattern: '222-11-0090'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[89]: value matches the ssn pattern: '222-11-0090'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[90]: value matches the ssn pattern: '222-11-0091'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[90]: value matches the ssn pattern: '222-11-0091'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[91]: value matches the ssn pattern: '222-11-0092'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[91]: value matches the ssn pattern: '222-11-0092'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[92]: value matches the ssn pattern: '222-11-0093'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[92]: value matches the ssn pattern: '222-11-0093'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[93]: value matches the ssn pattern: '222-11-0094'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[93]: value matches the ssn pattern: '222-11-0094'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[94]: value matches the ssn pattern: '222-11-0095'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[94]: value matches the ssn pattern: '222-11-0095'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[95]: value matches the ssn pattern: '222-11-0096'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[95]: value matches the ssn pattern: '222-11-0096'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[96]: value matches the ssn pattern: '222-11-0097'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[96]: value matches the ssn pattern: '222-11-0097'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[97]: value matches the ssn pattern: '222-11-0098'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[97]: value matches the ssn pattern: '222-11-0098'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[98]: value matches the ssn pattern: '222-11-0099'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[98]: value matches the ssn pattern: '222-11-0099'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[99]: value matches the ssn pattern: '222-11-0100'
      [pii-regex] $.profile.columns.token.value_counts_ext_order[99]: value matches the ssn pattern: '222-11-0100'
      [pii-regex] $.profile.columns.ssn.min_value[1]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.ssn.min_value[1]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.ssn.max_value[1]: value matches the ssn pattern: '222-11-0100'
      [pii-regex] $.profile.columns.ssn.max_value[1]: value matches the ssn pattern: '222-11-0100'
      [raw-string-list] $.profile.columns.ssn.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['222-11-0001', '222-11-0002', '222-11-0003']
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[0]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[0]: value matches the ssn pattern: '222-11-0001'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[1]: value matches the ssn pattern: '222-11-0002'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[1]: value matches the ssn pattern: '222-11-0002'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[2]: value matches the ssn pattern: '222-11-0003'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[2]: value matches the ssn pattern: '222-11-0003'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[3]: value matches the ssn pattern: '222-11-0004'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[3]: value matches the ssn pattern: '222-11-0004'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[4]: value matches the ssn pattern: '222-11-0005'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[4]: value matches the ssn pattern: '222-11-0005'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[5]: value matches the ssn pattern: '222-11-0006'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[5]: value matches the ssn pattern: '222-11-0006'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[6]: value matches the ssn pattern: '222-11-0007'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[6]: value matches the ssn pattern: '222-11-0007'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[7]: value matches the ssn pattern: '222-11-0008'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[7]: value matches the ssn pattern: '222-11-0008'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[8]: value matches the ssn pattern: '222-11-0009'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[8]: value matches the ssn pattern: '222-11-0009'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[9]: value matches the ssn pattern: '222-11-0010'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[9]: value matches the ssn pattern: '222-11-0010'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[10]: value matches the ssn pattern: '222-11-0011'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[10]: value matches the ssn pattern: '222-11-0011'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[11]: value matches the ssn pattern: '222-11-0012'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[11]: value matches the ssn pattern: '222-11-0012'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[12]: value matches the ssn pattern: '222-11-0013'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[12]: value matches the ssn pattern: '222-11-0013'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[13]: value matches the ssn pattern: '222-11-0014'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[13]: value matches the ssn pattern: '222-11-0014'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[14]: value matches the ssn pattern: '222-11-0015'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[14]: value matches the ssn pattern: '222-11-0015'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[15]: value matches the ssn pattern: '222-11-0016'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[15]: value matches the ssn pattern: '222-11-0016'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[16]: value matches the ssn pattern: '222-11-0017'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[16]: value matches the ssn pattern: '222-11-0017'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[17]: value matches the ssn pattern: '222-11-0018'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[17]: value matches the ssn pattern: '222-11-0018'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[18]: value matches the ssn pattern: '222-11-0019'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[18]: value matches the ssn pattern: '222-11-0019'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[19]: value matches the ssn pattern: '222-11-0020'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[19]: value matches the ssn pattern: '222-11-0020'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[20]: value matches the ssn pattern: '222-11-0021'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[20]: value matches the ssn pattern: '222-11-0021'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[21]: value matches the ssn pattern: '222-11-0022'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[21]: value matches the ssn pattern: '222-11-0022'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[22]: value matches the ssn pattern: '222-11-0023'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[22]: value matches the ssn pattern: '222-11-0023'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[23]: value matches the ssn pattern: '222-11-0024'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[23]: value matches the ssn pattern: '222-11-0024'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[24]: value matches the ssn pattern: '222-11-0025'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[24]: value matches the ssn pattern: '222-11-0025'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[25]: value matches the ssn pattern: '222-11-0026'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[25]: value matches the ssn pattern: '222-11-0026'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[26]: value matches the ssn pattern: '222-11-0027'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[26]: value matches the ssn pattern: '222-11-0027'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[27]: value matches the ssn pattern: '222-11-0028'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[27]: value matches the ssn pattern: '222-11-0028'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[28]: value matches the ssn pattern: '222-11-0029'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[28]: value matches the ssn pattern: '222-11-0029'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[29]: value matches the ssn pattern: '222-11-0030'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[29]: value matches the ssn pattern: '222-11-0030'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[30]: value matches the ssn pattern: '222-11-0031'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[30]: value matches the ssn pattern: '222-11-0031'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[31]: value matches the ssn pattern: '222-11-0032'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[31]: value matches the ssn pattern: '222-11-0032'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[32]: value matches the ssn pattern: '222-11-0033'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[32]: value matches the ssn pattern: '222-11-0033'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[33]: value matches the ssn pattern: '222-11-0034'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[33]: value matches the ssn pattern: '222-11-0034'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[34]: value matches the ssn pattern: '222-11-0035'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[34]: value matches the ssn pattern: '222-11-0035'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[35]: value matches the ssn pattern: '222-11-0036'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[35]: value matches the ssn pattern: '222-11-0036'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[36]: value matches the ssn pattern: '222-11-0037'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[36]: value matches the ssn pattern: '222-11-0037'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[37]: value matches the ssn pattern: '222-11-0038'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[37]: value matches the ssn pattern: '222-11-0038'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[38]: value matches the ssn pattern: '222-11-0039'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[38]: value matches the ssn pattern: '222-11-0039'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[39]: value matches the ssn pattern: '222-11-0040'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[39]: value matches the ssn pattern: '222-11-0040'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[40]: value matches the ssn pattern: '222-11-0041'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[40]: value matches the ssn pattern: '222-11-0041'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[41]: value matches the ssn pattern: '222-11-0042'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[41]: value matches the ssn pattern: '222-11-0042'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[42]: value matches the ssn pattern: '222-11-0043'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[42]: value matches the ssn pattern: '222-11-0043'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[43]: value matches the ssn pattern: '222-11-0044'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[43]: value matches the ssn pattern: '222-11-0044'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[44]: value matches the ssn pattern: '222-11-0045'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[44]: value matches the ssn pattern: '222-11-0045'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[45]: value matches the ssn pattern: '222-11-0046'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[45]: value matches the ssn pattern: '222-11-0046'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[46]: value matches the ssn pattern: '222-11-0047'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[46]: value matches the ssn pattern: '222-11-0047'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[47]: value matches the ssn pattern: '222-11-0048'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[47]: value matches the ssn pattern: '222-11-0048'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[48]: value matches the ssn pattern: '222-11-0049'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[48]: value matches the ssn pattern: '222-11-0049'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[49]: value matches the ssn pattern: '222-11-0050'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[49]: value matches the ssn pattern: '222-11-0050'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[50]: value matches the ssn pattern: '222-11-0051'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[50]: value matches the ssn pattern: '222-11-0051'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[51]: value matches the ssn pattern: '222-11-0052'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[51]: value matches the ssn pattern: '222-11-0052'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[52]: value matches the ssn pattern: '222-11-0053'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[52]: value matches the ssn pattern: '222-11-0053'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[53]: value matches the ssn pattern: '222-11-0054'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[53]: value matches the ssn pattern: '222-11-0054'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[54]: value matches the ssn pattern: '222-11-0055'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[54]: value matches the ssn pattern: '222-11-0055'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[55]: value matches the ssn pattern: '222-11-0056'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[55]: value matches the ssn pattern: '222-11-0056'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[56]: value matches the ssn pattern: '222-11-0057'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[56]: value matches the ssn pattern: '222-11-0057'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[57]: value matches the ssn pattern: '222-11-0058'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[57]: value matches the ssn pattern: '222-11-0058'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[58]: value matches the ssn pattern: '222-11-0059'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[58]: value matches the ssn pattern: '222-11-0059'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[59]: value matches the ssn pattern: '222-11-0060'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[59]: value matches the ssn pattern: '222-11-0060'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[60]: value matches the ssn pattern: '222-11-0061'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[60]: value matches the ssn pattern: '222-11-0061'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[61]: value matches the ssn pattern: '222-11-0062'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[61]: value matches the ssn pattern: '222-11-0062'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[62]: value matches the ssn pattern: '222-11-0063'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[62]: value matches the ssn pattern: '222-11-0063'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[63]: value matches the ssn pattern: '222-11-0064'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[63]: value matches the ssn pattern: '222-11-0064'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[64]: value matches the ssn pattern: '222-11-0065'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[64]: value matches the ssn pattern: '222-11-0065'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[65]: value matches the ssn pattern: '222-11-0066'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[65]: value matches the ssn pattern: '222-11-0066'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[66]: value matches the ssn pattern: '222-11-0067'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[66]: value matches the ssn pattern: '222-11-0067'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[67]: value matches the ssn pattern: '222-11-0068'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[67]: value matches the ssn pattern: '222-11-0068'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[68]: value matches the ssn pattern: '222-11-0069'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[68]: value matches the ssn pattern: '222-11-0069'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[69]: value matches the ssn pattern: '222-11-0070'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[69]: value matches the ssn pattern: '222-11-0070'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[70]: value matches the ssn pattern: '222-11-0071'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[70]: value matches the ssn pattern: '222-11-0071'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[71]: value matches the ssn pattern: '222-11-0072'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[71]: value matches the ssn pattern: '222-11-0072'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[72]: value matches the ssn pattern: '222-11-0073'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[72]: value matches the ssn pattern: '222-11-0073'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[73]: value matches the ssn pattern: '222-11-0074'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[73]: value matches the ssn pattern: '222-11-0074'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[74]: value matches the ssn pattern: '222-11-0075'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[74]: value matches the ssn pattern: '222-11-0075'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[75]: value matches the ssn pattern: '222-11-0076'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[75]: value matches the ssn pattern: '222-11-0076'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[76]: value matches the ssn pattern: '222-11-0077'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[76]: value matches the ssn pattern: '222-11-0077'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[77]: value matches the ssn pattern: '222-11-0078'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[77]: value matches the ssn pattern: '222-11-0078'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[78]: value matches the ssn pattern: '222-11-0079'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[78]: value matches the ssn pattern: '222-11-0079'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[79]: value matches the ssn pattern: '222-11-0080'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[79]: value matches the ssn pattern: '222-11-0080'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[80]: value matches the ssn pattern: '222-11-0081'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[80]: value matches the ssn pattern: '222-11-0081'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[81]: value matches the ssn pattern: '222-11-0082'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[81]: value matches the ssn pattern: '222-11-0082'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[82]: value matches the ssn pattern: '222-11-0083'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[82]: value matches the ssn pattern: '222-11-0083'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[83]: value matches the ssn pattern: '222-11-0084'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[83]: value matches the ssn pattern: '222-11-0084'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[84]: value matches the ssn pattern: '222-11-0085'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[84]: value matches the ssn pattern: '222-11-0085'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[85]: value matches the ssn pattern: '222-11-0086'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[85]: value matches the ssn pattern: '222-11-0086'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[86]: value matches the ssn pattern: '222-11-0087'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[86]: value matches the ssn pattern: '222-11-0087'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[87]: value matches the ssn pattern: '222-11-0088'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[87]: value matches the ssn pattern: '222-11-0088'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[88]: value matches the ssn pattern: '222-11-0089'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[88]: value matches the ssn pattern: '222-11-0089'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[89]: value matches the ssn pattern: '222-11-0090'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[89]: value matches the ssn pattern: '222-11-0090'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[90]: value matches the ssn pattern: '222-11-0091'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[90]: value matches the ssn pattern: '222-11-0091'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[91]: value matches the ssn pattern: '222-11-0092'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[91]: value matches the ssn pattern: '222-11-0092'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[92]: value matches the ssn pattern: '222-11-0093'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[92]: value matches the ssn pattern: '222-11-0093'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[93]: value matches the ssn pattern: '222-11-0094'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[93]: value matches the ssn pattern: '222-11-0094'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[94]: value matches the ssn pattern: '222-11-0095'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[94]: value matches the ssn pattern: '222-11-0095'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[95]: value matches the ssn pattern: '222-11-0096'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[95]: value matches the ssn pattern: '222-11-0096'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[96]: value matches the ssn pattern: '222-11-0097'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[96]: value matches the ssn pattern: '222-11-0097'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[97]: value matches the ssn pattern: '222-11-0098'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[97]: value matches the ssn pattern: '222-11-0098'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[98]: value matches the ssn pattern: '222-11-0099'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[98]: value matches the ssn pattern: '222-11-0099'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[99]: value matches the ssn pattern: '222-11-0100'
      [pii-regex] $.profile.columns.ssn.value_counts_ext_order[99]: value matches the ssn pattern: '222-11-0100'
      [raw-string-list] $.profile.columns.salary.value_counts_ext_order: list under 'value_counts_ext_order' carries 100 raw strings (more than 2): a value dump. Sample: ['50001', '50002', '50003']
      [raw-string-list] $.profile.joint.columns: list under 'columns' carries 14 raw strings (more than 2): a value dump. Sample: ['amount', 'churned', 'customer_email']
      [raw-string-list] $.profile.joint.categorical_columns: list under 'categorical_columns' carries 12 raw strings (more than 2): a value dump. Sample: ['churned', 'customer_email', 'customer_id']
    ```

This command exits nonzero. Read the diagnostic; this transcript shows a refusal or failed check, not a passing gate.
