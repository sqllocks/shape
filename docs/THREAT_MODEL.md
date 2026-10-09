# Threat model

Status: experimental.


Review the shipped source and tests for each control. [Owner: security reviewer — confirm this
threat model against the deployment before publishing an assurance statement.]

## Assets

- Source data and the real values inside a full-capture profile (`.shape`, `--json` written with `--capture full`).
- Safe profiles (`shape profile safe`): the artifact that is meant to be shared and committed.
- Vaulted values (`.shapevault`, W5-03): the values a safe capture withheld, encrypted in a
  separate file; and the key-encryption key (KEK) and the data key that protect them.
- Synthetic output: files, tables, event streams.
- Credentials: connection strings, SAS tokens, SASL and SQL passwords, Entra tokens, Ed25519
  private keys, AES-GCM keys.
- The integrity of artifacts, packs, reference datasets, the registry and the baseline-free
  release pipeline.
- The machine Shape runs on (files it can write, commands it can start).

## Trust boundaries and who is trusted

| Boundary | Trusted? |
|---|---|
| The user running the CLI and its arguments | Yes. A self-inflicted argument is not an attack (`--command` of `git-setup`, output paths chosen with `-o`). |
| Installed plugins | Yes, in-process code (`docs/plugins/trust-model.md`). No sandbox. |
| `.shape` files, profile and safe-profile JSON, contracts, generation schemas, scenario packs, GSL YAML, DDL, DB and stream payloads, reference datasets, JSONL/CSV inputs | **No.** Anyone can hand a user one of these. This is where the review looked hardest. |
| Pipeline parameters (ADF, Fabric notebook parameters) | Partly. They come from whoever can trigger a pipeline, so they are not spliced into shell or code. |
| Remote services (Kafka, Event Hubs, Eventhouse, SQL Server, ADLS) | Their responses are untrusted input; Shape sends only what the schema and profile produce, quoted for the target. |
| CI and release pipeline | Out of scope of the code; see deployment controls. |

## Threats and controls by surface

Each row: the threat, the control, the test that enforces it.

### `.shape` container, manifests, signatures

| Threat | Control | Test |
|---|---|---|
| Path traversal and unsafe member names | `_safe()` rejects absolute, `..`, `.`, backslash, NUL, drive and empty parts on read and write; members are never extracted to disk | `tests/security/test_ga_security.py`, `tests/validation/test_fuzz_artifact.py` |
| Zip bombs, oversized or many members | member count, per-member, total and ratio limits; the manifest is capped at 4 MiB; fails closed with `ArtifactError` | `tests/security/test_ga_security.py` |
| A bomb read outside the limits (a kind sniff, the registry's manifest read, a signature member) | one bounded reader, `read_manifest_bytes`; the signature member's size is checked before it is read | `tests/security/test_p7_04_review.py` (bomb, signature cases) |
| Tampering with hashes | content hashes plus the content id are checked on read | `tests/artifact/test_artifact.py` |
| Forged artifact with rewritten hashes | Ed25519 signature over the exact manifest bytes with a domain prefix; stripped, wrong-key and re-signed files fail (`shape verify`, `--verify`) | `tests/artifact/test_signing.py` |
| `--verify` skipped by renaming the file | the CLI decides by content (a zip with a manifest), not by extension | `tests/security/test_p7_04_review.py` |
| Stored (uncompressed) members of the reproducible container | the readers accept every compression method and apply the same limits | `tests/artifact/test_container_reproducible.py` |
| Non-reproducible bytes hiding a change in git | fixed timestamps, order, attributes, stored members | `tests/artifact/test_container_reproducible.py` |
| Malformed anything | the artifact fuzzer (below) | `tests/validation/test_fuzz_smoke.py` and the nightly job |

### Parsing of untrusted documents

| Threat | Control | Test |
|---|---|---|
| Unsafe YAML (object construction) | `yaml.safe_load` only, through one loader | `tests/security/test_p7_04_review.py`, grep gate below |
| YAML alias bomb, huge or deeply nested YAML | `shape.security.yamlsafe`: 4 MiB cap, expanded-size cap, recursive aliases refused, `RecursionError` turned into a rejection; used by packs, GSL, contracts and schema files | `tests/security/test_p7_04_review.py` |
| Deeply nested or oversized profile JSON | `validate_structure` (depth 64, 1M items) on `read_model` and on profile load | `tests/security/test_ga_security.py`, fuzzer |
| Quadratic regex on a long string in a safe profile | the e-mail pattern starts only at the start of a local-part run | `tests/validation/test_fuzz_smoke.py` |
| Cubic regex on DDL, quadratic regex on rule text | names exclude bare spaces; rule text is capped | `tests/security/test_p7_04_review.py` |
| A JSONL file that crashes pyarrow (segfault on deep nesting) | a per-line depth check before pyarrow reads a file | `tests/security/test_p7_04_review.py` |
| A Parquet decompression bomb (a few KB declaring 100M rows) exhausts memory | optional `SHAPE_MAX_INPUT_ROWS` and `SHAPE_MAX_INPUT_BYTES`, checked against the footer before any data page is read (`docs/PROFILING_NOTES.md`); unset, the process memory limit is the control (R8) | `tests/io/test_input_budget.py` |
| A poison stream message stops the consumer | `RecursionError` is a decode failure, skipped or raised per `on_error` | `tests/security/test_p7_04_review.py` |
| Pattern widths that exhaust memory | `{random:N}`-style widths are bounded | `tests/security/test_p7_04_review.py` |
| Deserialization | no pickle, marshal or `eval` on data anywhere in `src` or the plugins; the formula strategy is an `ast` allow-list; `shape query` is a regex-limited dictionary walk | `bandit -r src` (P7-04 review), `tests/validation/test_requirements.py` |

### Paths: where Shape writes

| Threat | Control | Test |
|---|---|---|
| A table name in a schema, profile or artifact used as a file name (`../x`, `/abs/x`) | `shape.security.names`: refused when the schema is parsed, and checked again at every sink and writer (files, SQL, Excel, Delta, CDM, dimensional, `jsonl://` emitter, incremental) | `tests/security/test_p7_04_review.py` |
| Pack landing paths, topic and event names | `unsafe_path` and `unsafe_name`; the runner re-checks containment | `tests/scenario`, `tests/packs` |
| Registry names and refs | name regexes plus a resolved-root check; objects are written atomically and verified on checkout; no fixed temp name | `tests/registry/test_local_registry.py`, `tests/security/test_p7_04_review.py` |
| Reference dataset name used as a path to read a JSON file | the name must be a plain name | `tests/security/test_p7_04_review.py` |
| `shape git-setup` writing through a symlinked `.gitattributes`, or a pattern that adds attribute lines | the symlink is refused, a pattern with whitespace is refused; the command is stored as one escaped git config value, git runs with an argument list | `tests/security/test_p7_04_review.py`, `tests/cli/test_shape_as_code.py` |
| `shape mask` overwriting its inputs | it refuses, and writes only inside `-o` | `tests/security/test_p7_04_review.py` |
| Key files | private key created with `O_EXCL` and mode 0600; never overwritten | `tests/artifact/test_signing.py` |

### Injection into sinks and generated code

| Threat | Control | Test |
|---|---|---|
| SQL injection through identifiers and values in the SQL sink | identifiers quoted per dialect, values escaped per dialect, DDL dimensions must be integers | `tests/generation/test_writers.py`, `tests/security/test_p7_04_review.py` |
| KQL injection in the Eventhouse writer | `_q()` escapes names; the ingestion mapping literal escapes backslashes and quotes | `plugins/shape-fabric/tests`, `tests/security/test_p7_04_review.py` |
| SQL injection in the SQL Server plugin | catalog queries use parameters; identifiers go through `quote_ident`; connection-string values are brace-quoted | `plugins/shape-sqlserver/tests` |
| Command injection through a pipeline parameter | the ADF Batch command single-quotes the image after stripping quotes; the notebooks and the Fabric UDF embed no parameter in code or SQL | `tests/security/test_p7_04_review.py`, `tests/demo/fabric` |
| Spreadsheet formula injection in CSV or Excel output | **not** mitigated (residual risk R3) | |

### Secrets

| Threat | Control | Test |
|---|---|---|
| Secrets stored in an artifact | `enforce_no_secrets` on every write: private keys, cloud keys, SAS signatures, JWTs, bearer tokens, `password`/`secret`/`api_key` assignments in JSON or text | `tests/security/test_p7_04_review.py`, `tests/security/test_ga_security.py` |
| Secrets in errors and logs | connection strings are redacted (quoted values and client secrets included); the run log holds no arguments or URIs; `Credentials.__repr__` masks the secret | `plugins/shape-sqlserver/tests`, `tests/security/test_p7_04_review.py` |
| Key misuse | AES-256-GCM with a fresh 96-bit nonce and the header as AAD; Ed25519 verification fails closed on any error | `tests/artifact/test_secure.py`, `tests/security/test_crypto.py` |

### Privacy of profiles

Raw-value leakage, re-identification and small cells are handled by the safe profile, the leak
validator and k-release risk (`docs/PRIVACY_MODEL.md`, `tests/privacy`). A full-fidelity `.shape`
and `--json` (`--capture full`) hold real values by design; the default capture of `shape profile`
is the committable artifact (`docs/PRIVACY_MODEL.md`), and `shape profile validate --safe` flags a
full one.

### Mistakes: chaos on real data, writes to the wrong target

Two checks prevent a user from doing harm by accident; neither is a security boundary.

- **Chaos input** (`docs/CHAOS.md`). `shape chaos --input` refuses table files that
  `_shape_provenance.json` does not list with a matching SHA-256 (or Parquet without the
  `shape_synthetic` key). It stops corrupting a folder of production extracts by mistake. It does
  not stop a user who passes `--allow-real-input`, copies a sidecar, writes the marker themselves
  or edits the sidecar; the check is provenance, not content, and the sidecar is not signed.
- **Non-local targets** (`docs/SINKS.md`). A `--to`, `emit --sink` or `scale --sink` target that
  is not on this machine needs `--yes` or `SHAPE_CONFIRM_REMOTE=1`. It stops a mistyped or
  stale URI from writing to a real system. It does not stop a script that sets the variable, and
  it does not limit what a plugin command (`shape fabric publish`) writes.

### The value vault

`docs/VAULT.md` states the model. Protected: the confidentiality and integrity of vaulted values at
rest and in transit against anyone who does not hold the KEK. Not protected: a holder of the KEK
and the vault, values after decryption (memory, and data generated in vault mode), a compromised
machine, metadata in the clear (column names, policies, ciphertext sizes, the key id), and old
copies of a vault after rotation.

| Threat | Control | Test |
|---|---|---|
| Vaulted values readable from the vault, the profile, `--json`, stderr or an error | AES-256-GCM envelope encryption (`cryptography`, no custom primitive); only withheld values enter the vault; the planted values are in no output but the decrypted vault | `tests/vault/test_generate.py::test_leak_planted_values_are_in_no_output_but_the_decrypted_vault`, `tests/vault/test_build.py` |
| A weak or reused key or nonce | a fresh 256-bit data key from `os.urandom` and a fresh 96-bit nonce per call; two writes of one profile share no data key, nonce or ciphertext | `tests/vault/test_format.py::test_two_writes_differ_in_every_secret_part`, `tests/vault/test_build.py::test_two_writes_of_one_profile_differ` |
| A format only this program can read, or a mistake in it | standard AES-GCM envelope; an independent decrypt written from the documented format with `AESGCM` alone | `tests/vault/test_format.py::test_independent_decrypt_from_the_documented_format` |
| A changed header, wrapped key, nonce or ciphertext; truncation | the whole header is the associated data of every call; any change fails authentication (exit 1, no value printed) | `tests/vault/test_format.py` (one changed character in each part, truncation), `tests/vault/test_ops.py::test_any_changed_byte_of_the_vault_is_a_mismatch_not_a_crash` |
| A column moved to another column, vault or profile | the column name, its policy, the vault id and the profile content id are in the associated data | `tests/vault/test_format.py` (swap, rename, drop, another vault, another profile) |
| Substituting another vault for the one a signed profile names | the profile manifest records `vault_id` and SHA-256; the signature covers `manifest.json`; `shape generate --vault` and `shape vault verify` refuse a mismatch before decrypting | `tests/vault/test_ops.py`, `tests/vault/test_generate.py` |
| The wrong key | `kek_id` names both ids and never a key; exit 1 | `tests/vault/test_format.py::test_wrong_kek_names_both_ids_and_no_key`, `tests/vault/test_cli.py` |
| The KEK in a command line, a log, a message or a readable file | `--kek` takes a reference only (a literal key is refused without being echoed); `file://` refuses a group- or world-readable file; no message carries a key or a value | `tests/vault/test_kek.py`, `tests/vault/test_cli.py` |
| A vault or KEK committed to git | `profile --vault` and `keygen` refuse (exit 2) a path inside an unignored git work tree and name the `.gitignore` line; `shape git-setup` adds `*.shapevault` | `tests/vault/test_ops.py`, `tests/vault/test_build.py` |
| A vault written with the values already in the clear | `--vault` with `--capture full` exits 2 | `tests/vault/test_generate.py::test_profile_command_vault_with_capture_full_is_exit_2` |
| A vault run mistaken for shape-only output | the run records `generation_mode: "shape+vault"` and the vault id; one warning on stderr; `--dry-run` and `shape plan` mark the columns `vault` | `tests/vault/test_generate.py` |
| Vault files readable by other users | vaults and key files are written with mode 0600 (POSIX) | `tests/vault/test_ops.py::test_vault_files_are_private`, `tests/vault/test_kek.py` |

Residual risks of the vault: V1. A holder of the KEK and the vault reads every vaulted value. V2.
Generated data in vault mode holds real values. V3. Column names, policies, ciphertext sizes and the
key id are in the clear. V4. An old vault still opens with the old KEK after `rekey`; rotate by
deleting the old copies. V5. Key storage, backup and revocation are the user's: a lost KEK means
the values are gone. V6. A process that holds a decrypted value (or the KEK) in memory is trusted.

### Network surfaces

Emitters (Kafka, Event Hubs, Fabric Eventstream and Eventhouse), `shape stream` sources, SQL Server
and ADLS access are outbound only; Shape opens no listening socket. Credentials come from the
environment, an options file or Entra, never from a profile or pack. Zero-network behaviour of the
offline commands is enforced by `tests/security/test_zero_network.py`, and by the `zero_network`
fixture that guards every test that needs no network (`docs/INSTALL.md`, offline installs).

## The artifact fuzzer

`shape.validation.fuzz` (driver `scripts/fuzz_artifacts.py`) mutates valid seeds for `.shape`
containers, signatures, manifests, profile artifacts, safe-profile JSON, contracts and pack and GSL
YAML. A target may accept an input or raise one of its documented rejection types
(`ShapeError`, `ValueError`, `BadZipFile`); any other exception or a run over five seconds is a
finding. It is deterministic per seed. A short seeded run is in the normal test suite
(`tests/validation/test_fuzz_smoke.py`); the nightly workflow (`artifact-fuzz`) runs it with a
fresh seed and uploads each finding's input.

## Residual risks (accepted, with reasons)

- **R1. Plugins are trusted code.** There is no sandbox; confinement is a deployment control.
- **R2. An unreasonable scale is a resource request, not a defect.** A profile that states
  `row_count: 10**12` makes `generate` try to produce it. Cap it with the deployment (memory and
  time limits) or `--rows`.
- **R3. Formula injection in CSV and Excel output.** Synthetic strings that start with `=`, `+`,
  `-` or `@` are written as they are. Open untrusted synthetic output as text.
- **R4. `--command` of `git-setup` is run by git through a shell**, as the user chose.
- **R5. The secret scanner is best effort.** It matches known shapes, not every secret.
- **R6. URIs are echoed.** A URI with credentials in its userinfo or query string appears in
  stdout and checkpoints; pass credentials through the environment or an options file.
- **R7. Signing is opt-in.** An unsigned artifact has only accident-level integrity.
- **R8. Input size is bounded only on request.** The input budgets are off by default, and a
  compressed CSV or JSONL file or a dictionary-encoded Parquet string column can decode to much more
  than its size on disk. Set `SHAPE_MAX_INPUT_ROWS` and `SHAPE_MAX_INPUT_BYTES` and a process memory
  limit when profiling files from someone else.

## Deployment controls still required

OS and container confinement of the whole process, a secret manager or KMS, egress policy, access
control, audit logging, dependency provenance and pinning of CI actions, backup and recovery,
target-service authentication and independent assessment.
