# Snowflake write receipt

Status: available.

Needs a Snowflake account. Not run in CI. Commands and output are historical evidence, not commands executed by the docs harness.

Private account identifiers, service usernames, workspace names and key-file paths are replaced with angle-bracket placeholders where present. Public GitHub run, job and ruleset IDs remain so you can trace the results. Recorded findings, dates, row counts and exit statuses are unchanged.

Owner-run receipt supplied by the Owner on **2026-10-11**. The documentation editor did not independently execute this run or query the service. Commands, output and persisted row counts below record the Owner’s supplied evidence; install commands are supplied setup details, not complete pip logs. Private-key contents and token values are not recorded.


## Environment and installation

- Clean Windows 11 VM; Python **3.12.10**.
- Shape core, domains and databases plugin **0.9.1**, installed from PyPI.
- `snowflake-connector-python` **4.8.0**.

```text
pip install "sqllocks-shape[domains]==0.9.1" "sqllocks-shape-databases[snowflake]==0.9.1"
```

## Target and authentication

- Account `<account>`, Azure Central US, Standard trial.
- Service user `<user>`, `TYPE=SERVICE`; key-pair sign-in.
- Role `<role>`; warehouse `<warehouse>`, X-Small.
- Database `<database>`; schema `<schema>`.
- Private key reference: `file://<private-key-path>`, passed as `--sink-config snowflake.private_key=...`; no key contents included.
- `SHAPE_CONFIRM_REMOTE=1` was set; the command also supplied `--yes`.

## Captured replace run

The Owner places the run at approximately **6:50 PM Eastern on 2026-10-11**. The VM clock was wrong for this run; no VM timestamp is presented as the run’s actual time. A prior `--write-mode create` run also exited 0, according to the Owner; the replace run is the captured receipt below.

Command (with the separately supplied key reference expanded):

```text
shape generate retail --scale small --seed 42 --to "snowflake://<user>@<account>/<database>/<schema>?warehouse=<warehouse>&role=<role>" --write-mode replace --yes --sink-config "snowflake.private_key=file://<private-key-path>"
```

Supplied output:

```text
shape 0.9.1
Wrote 21,750 rows in 9 tables to snowflake://<user>@<account>/<database>/<schema>?warehouse=<warehouse>&role=<role> (19.51s)
```

Exit code: **0**. The elapsed time is one run’s output, not a benchmark or throughput claim.

## Persisted-row verification

The Owner verified these counts in Snowsight as `<role>`, using `<database>.INFORMATION_SCHEMA.TABLES` for schema `<schema>`. Table names were lower case and quoted. These are the supplied metadata counts, not a fabricated `COUNT(*)` transcript.

| Table | Persisted rows |
|---|---:|
| `address` | 1,500 |
| `customer` | 1,000 |
| `order` | 5,000 |
| `order_line` | 12,500 |
| `product` | 500 |
| `product_category` | 50 |
| `promotion` | 200 |
| `return` | 850 |
| `store` | 150 |
| **Total** | **21,750** |


## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
