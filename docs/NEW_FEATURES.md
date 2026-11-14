# New feature workflows since 0.9.3

Status: experimental, except contract checks (available).

Start with [daily order checks](DAILY_ORDERS.md) for regex and relative date bounds.
This page runs the other local workflows from a checkout with the
[locked example environment](contributing/EXAMPLES.md). Start in an empty directory;
`SHAPE_DOCS_REPO` points to your checkout. No cloud account is used.
The [example source](../examples/docs_new_features.py) contains the complete requests and
assertions, including the filesystem checks behind each printed summary.

## Locale names

The `locale` generator accepts `locale` and `provider` in each column's `generator`.
This exercise writes `names.gen.json` and generates three rows with seed 7 using every
new name list. Read [Locales](LOCALES.md) for licences, coverage and providers.
GB full names now use both Scottish lists, rather than falling back to US surnames.
CA/AU/DE have first names only; FR adds surnames to its existing first names.
Lists are sampled uniformly, not by population frequency. Regional sources do not represent
an entire country's population. Accents are retained where the upstream source supplies them.

<!-- example: 0 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/examples/docs_new_features.py" locales
```

??? info "Output (exit 0)"

    ```text {.expected}
    [{"au_first_name": "Lyla", "ca_first_name": "ADRIAN", "de_first_name": "August", "fr_last_name": "JALLET", "gb_first_name": "Dexter", "gb_last_name": "BEVERIDGE", "gb_name": "Maelie BARRIE"}, {"au_first_name": "Nevaeh", "ca_first_name": "MINHA", "de_first_name": "Annery", "fr_last_name": "DOUAY", "gb_first_name": "Davyd", "gb_last_name": "MCLAY", "gb_name": "Mayla CROMBIE"}, {"au_first_name": "Parker", "ca_first_name": "LOCHLAN", "de_first_name": "Alea", "fr_last_name": "TURC", "gb_first_name": "Angel-Rose", "gb_last_name": "JOHNSTON", "gb_name": "Hermes ROBB"}]
    ```

## Apply accepted rules (bridge 1.5)

Prepare a decision file with one accepted and one rejected rule. Existing contracts are
merged, not replaced. Pending, deferred and stale proposals do not become rules.

<!-- example: 1 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/examples/docs_new_features.py" rules
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared one accepted and one rejected rule; existing row_count.min=1
    ```

<!-- example: 2 -->

```bash {.runnable-reference}
shape rules apply rules.contract.json --decisions decisions.json --json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"applied": 1, "contract": "rules.contract.json", "rejected": 1, "format": "shape-result", "version": 1, "command": "rules apply", "exit_code": 0}
    ```

Repeating the application is idempotent: no new rule is applied and the contract bytes stay
unchanged. A conflicting rule is refused before writing. Bridge clients send
`{"api_version": "1.5", "command": "rules_apply", "args": {"decisions": "decisions.json", "contract": "rules.contract.json"}}`.
The result counts newly applied proposals and rejected rule decisions.

<!-- example: 3 -->

```bash {.runnable-reference}
shape rules apply rules.contract.json --decisions decisions.json --json
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"applied": 0, "contract": "rules.contract.json", "rejected": 1, "format": "shape-result", "version": 1, "command": "rules apply", "exit_code": 0}
    ```

## Run suites on selected tables and read the verdict

Bridge 1.5 `suite_run` accepts `input` (local table folder) and `name` (result label).
The exercise generates small retail fixtures once, selects those files, runs clean and
null-injected scenarios and asserts that the input bytes are unchanged. Defects and
validation gates still execute; answer-key `met` means the scenario behaved as expected,
so a scenario designed to fail a gate can still have `met: true`.
All domain tables must be present. Drift scenarios refuse selected inputs before starting
a job because they need generated day-by-day data.

Bridge 1.5 `report_card` accepts `source`/`path` as aliases for `real`/`synthetic`.
The example uses the same synthetic fixture on both sides solely to exercise the API.
`passed` is a boolean equal to `overall == "pass"`; it is not the bridge envelope's `ok`.
A successfully computed failing report still has envelope `ok: true`.
Do not treat this fixture comparison as privacy certification of a real dataset.

<!-- example: 4 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/examples/docs_new_features.py" suite
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"inputs_unchanged": true, "scenarios": [{"met": true, "scenario": "clean_baseline"}, {"met": true, "scenario": "nulls_injected"}], "suite": "daily"}
    {"overall": "pass", "passed": true}
    ```

## Manage a vault, including writes

The script creates a disposable vault containing synthetic categories and random keys.
`vault_status`, `vault_verify`, `vault_rewrap` and policy-file lookup arrived in bridge 1.4
and remain available in 1.5/1.6. Status reads metadata without authenticating values.
Verify checks the profile reference; supplying `kek` also authenticates the encrypted payload.
An optional `verify` public-key file checks the profile signature.

`vault_rewrap` rotates both the data key and wrapping key and writes a new profile/vault
pair. Its dry run validates and lists writes without creating files. Existing output paths
are refused. A Git worktree must ignore vault output; the example writes `.gitignore`.
Rotation drops an existing profile signature unless you supply a signing `key` and any
required environment passphrase. The original pair stays intact.

Policy-file lookup resolves explicit column, then classification, then default policy.
Bridge 1.6 adds authenticated mutation: `vault_policy` takes `vault`, `kek=env://NAME`,
`column=TABLE.COLUMN` and `new_policy` (`none`, `categories`, `extremes` or `all`).
Do not send `policy` or `classification` in mutation mode. Mutation authenticates the entire
vault and atomically replaces it; failures before replacement keep the original bytes.
Changing a policy does not recapture or erase the retained encrypted payload. A profile
referencing the old vault hash fails verification until its reference is refreshed.
The script checks that failure explicitly. Responses contain metadata, never keys or values.

<!-- example: 5 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/examples/docs_new_features.py" vault
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"status": true, "columns": ["orders.status"]}
    Original profile/vault reference and authenticated payload: verified
    Rotation: dry run writes nothing; actual write creates a verified new pair
    Policy-file lookup: categories
    Authenticated policy write: categories -> none; retained payload is unchanged
    Old profile reference after policy write: fails verification
    ```

Earlier bridge minors retain their result contracts: requests served as 1.0–1.4 refuse
the new 1.5 arguments, and pre-1.6 requests refuse policy mutation arguments. Always send
`api_version`. See [Bridge](BRIDGE.md) and [Value vault](VAULT.md) for the full protocol.

## Storage and script migration

Continue with [SQL and Delta fidelity](TYPE_FIDELITY.md) for local DDL and constraint checks,
[new sources](NEW_SOURCES.md) for account-dependent reads, and the
[`shape plan` migration](PLAN_MIGRATION.md) before upgrading a script that parses its output.
