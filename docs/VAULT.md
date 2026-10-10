# The value vault

Status: experimental.

The default `.shape` is safe to commit: a sensitive column keeps statistics and formats only, and
a category is kept only where every released category has enough rows (`docs/PRIVACY_MODEL.md`).
Some users still need the exact values back when they generate: the real list of status codes,
product categories or region names, with their frequencies. The only way to keep them in the
profile is `--capture full`, which puts them in the clear in the file that gets committed.

A **value vault** is the alternative: a separate, encrypted file that holds the values the safe
capture withheld, chosen per column by a policy, named in the signed profile by its hash, and read
only when a generation run asks for it. The `.shape` stays plain and committable.

It needs the `cryptography` package (extra `[sign]`).

## Files

| File | Kind | Declares |
|---|---|---|
| `X.shape` | the safe profile; its manifest gains `vault: {"vault_id", "sha256"}` | `format: shape`, profile artifact version 2 |
| `X.shapevault` | the vault | `format: shape-vault`, `version: 1` |
| `POLICY.json` | the policy | `format: shape-vault-policy`, `version: 1` |
| `KEK.key` | the key-encryption key | 32 random bytes, base64 |

Every persisted format here declares `format` and an integer `version` and follows
`docs/specs/STATE_AND_COMPATIBILITY.md`: a vault or policy from a newer release fails naming the
minimum Shape release that reads it (exit 2). The `vault` field of the profile manifest is additive:
a profile written before it (no `vault`) reads, verifies and generates as before.

## The vault format

```json
{
  "format": "shape-vault", "version": 1, "shape_version": "...", "min_shape_version": "...",
  "vault_id": "<128 random bits, hex>",
  "profile_content_id": "<the shape_content_id of the profile it belongs to>",
  "algorithm": "AES-256-GCM",
  "kek_id": "<first 16 hex of SHA-256 of the key-encryption key>",
  "wrapped_key": {"nonce": "<base64, 12 bytes>", "ciphertext": "<base64>"},
  "columns": {"TABLE.COLUMN": {"policy": "...", "nonce": "<base64>", "ciphertext": "<base64>"}}
}
```

Envelope encryption, with nothing custom:

1. A fresh 256-bit **data key** from `os.urandom` for every vault written.
2. Each column's payload (canonical JSON, `shape.artifact.canonical`) is encrypted with AES-256-GCM
   (`cryptography`'s `AESGCM`) under the data key with a fresh random 96-bit nonce.
3. The data key is encrypted with AES-256-GCM under the **key-encryption key** (KEK), also with a
   fresh nonce.

The associated data of every call binds the whole header, so a column ciphertext that is moved to
another column, another vault or another profile fails authentication:

```
prefix           = b"shape-vault-v1\x00"
header           = canonical_json({format, version, vault_id, profile_content_id, kek_id,
                                   columns: sorted column names})
aad(wrapped key) = prefix + b"key\x00" + header
aad(column)      = prefix + b"column\x00" + header + b"\x00"
                   + canonical_json({"name": NAME, "policy": POLICY})
```

`tests/vault/test_format.py::test_independent_decrypt_from_the_documented_format` decrypts a vault
with `AESGCM` and these fields alone.

`kek_id` names a wrong key without revealing it. Canonical JSON has no floats, so a number in a
payload is `{"float": "<repr>"}` (`shape.vault.format.encode_value`).

### What a column holds

Only what the safe capture withheld (its `redaction_manifest`), chosen by the policy:

| Policy | Payload |
|---|---|
| `categories` | the column's category table as profiled, when the safe capture suppressed or folded it: `enum_values` and `value_counts_ext`, each a list of `[value, row count]`, plus `dtype` and the non-null `rows` |
| `extremes` | the raw `min` and `max` (each `[type, value]`), when the safe capture suppressed them |
| `all` | both |
| `none` | nothing |

A column the safe capture already releases in full gets no vault entry; a policy that asks for
something that was not withheld writes nothing for that column. Counts are the profile's shares
times the column's non-null rows (the profile keeps shares, rounded to its own precision).

## The policy file

```json
{"format": "shape-vault-policy", "version": 1,
 "default": "none",
 "by_classification": {"CONFIDENTIAL": "categories"},
 "columns": {"orders.status": "categories", "orders.amount": "extremes"}}
```

An explicit `columns` entry wins over `by_classification` (the ordered taxonomy of
`docs/PRIVACY_MODEL.md`; `PII` and `SENSITIVE` stand for `CONFIDENTIAL`), which wins over `default`.
A column's classification is the one declared when the profile is written (`--classify
COLUMN=LEVEL`). A column that is not in the profile, an unknown policy value or an unknown
classification exits 2 and names it.

## Commands

<!-- example: 3 -->

Syntax reference. Replace the named arguments with your inputs.

```bash
shape vault keygen -o KEK.key
shape profile orders.csv -o orders.shape --classify email=CONFIDENTIAL \
      --vault orders.shapevault --vault-policy policy.json --kek file://KEK.key
shape vault inspect orders.shapevault
shape vault verify orders.shapevault --shape orders.shape --kek file://KEK.key --verify PUBKEY
shape vault rekey orders.shape orders.shapevault --kek file://OLD.key --new-kek file://NEW.key \
      --out-shape orders2.shape --out-vault orders2.shapevault [--key SIGNING_KEY] [--dry-run]
shape generate --from orders.shape --vault orders.shapevault --kek file://KEK.key -f csv -o out
```


The Python API is `shape.save(profile, path, capture="safe", vault=PATH, vault_policy=POLICY,
kek=KEK)` (`kek` is 32 bytes or a credential reference).

- **`keygen`** writes 32 random bytes, base64, mode 0600 where the OS has mode bits, and never
  overwrites a file.
- **`--kek REF`** is `env://NAME` (base64 of 32 bytes), `file://PATH` or a plain path, or any other
  registered credential scheme such as `kv://` (`shape.security.credrefs`). A KEK file that group or
  others can read is refused. The KEK is never accepted as a literal command-line value, and no
  command prints, logs or puts the KEK, the data key or a decrypted value in a message.
- **`profile --vault`** writes the safe capture as usual and the vault next to it, vault first, then
  the manifest with `vault: {"vault_id", "sha256"}` (SHA-256 of the vault file's bytes). `--vault`
  with `--capture full` exits 2. When the vault or the KEK file is inside a git work tree and git
  does not ignore it, the command exits 2 and names the `.gitignore` line to add; `shape
  git-setup` adds `*.shapevault` to `.gitignore`.
- **`inspect`** prints the header only (vault id, profile id, key id, each column's policy and
  ciphertext size) and needs no key.
- **`verify`** checks the vault's SHA-256 and `vault_id` against the profile's `vault` reference,
  `profile_content_id` against the profile, the profile's signature when `--verify` is given and,
  with `--kek`, that the data key and every column authenticate.
- **`rekey`** decrypts with the old KEK, re-encrypts every column under a new data key and the new
  KEK with new nonces and a new `vault_id`, and writes a new vault and a profile whose `vault`
  reference points to it. The profile components are byte-identical; the signature is dropped
  (a notice says so) unless `--key` signs the new file. The inputs are never changed in place and
  existing outputs are never overwritten. `--dry-run` lists the two writes.

Both `verify` and `rekey` carry `--json`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | valid, or done |
| 1 | the wrong KEK (both key ids are named), a hash, vault id, profile id or signature mismatch, or an authentication failure |
| 2 | a malformed vault, a newer `version` (the message names the minimum Shape release), a KEK that does not decode to 32 bytes or is readable by others, an unusable policy, a path in an unignored git work tree, any other bad input |

A vault changed in any way is a mismatch (exit 1) when the profile records its hash, whatever state
the bytes are in.

## Generation modes

- **Shape only** (`shape generate --from X.shape`): statistical, from what the profile holds. The
  output is byte-identical to what the same run wrote before the vault existed
  (`tests/vault/test_generate.py`), whether or not the profile names a vault.
- **Shape plus vault** (`--vault VAULT --kek REF [--verify PUBKEY]`): a `categories` column draws
  from the exact category values and frequencies in the vault; an `extremes` column uses the raw
  minimum and maximum as bounds; every other column is generated as in shape-only mode. Columns
  that are keys, in a correlation or temporal keep their strategy (bounds apply to numbers and
  dates). Before decrypting, the vault is checked as `verify` does, and a vault that does not match
  the profile is refused (exit 1). The run records `generation_mode: "shape+vault"`, the
  `vault_id` and the vaulted columns (in `--json` and in the run metrics); `--dry-run` and `shape
  plan` mark those columns `vault`; stderr prints once `shape: warning: output generated with
  VAULT contains real values from the vault; treat it like the source data`.

The data generated in vault mode contains real values. Treat it like the source data.

## Threat model

**Protected.** The confidentiality and integrity of vaulted values at rest and in transit (a leaked
repository, CI artifact, backup or copied file) against anyone who does not hold the KEK.
Tampering, truncation, column swapping and substitution of another vault are detected: AES-GCM tags
with bound associated data, and the vault hash in the signed profile.

**Not protected.**

- Anyone who holds the KEK and the vault.
- Values after decryption: in memory, and in data generated in vault mode, which contains real
  values.
- A compromised machine that runs Shape.
- Metadata in the clear: column names, policies, approximate payload sizes (from ciphertext
  length), the key id.
- Old copies of a vault after rotation, which still open with the old KEK.

Key storage, backup and revocation are the user's responsibility; a lost KEK means the values are
gone. The controls, each with the test that enforces it, are in `docs/THREAT_MODEL.md`.

## Out of scope

Key services and hardware keys beyond the credential reference resolvers, passphrase-derived KEKs,
key escrow, secret sharing and multi-recipient vaults; encrypting the profile itself; any change to
what the safe capture releases; vaulting values of models written by `shape capture` or of the safe
profile JSON written by `shape profile safe`.
