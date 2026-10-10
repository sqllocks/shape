# Signing `.shape` artifacts

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" SIGNING
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for SIGNING
    ```


A `.shape` file carries SHA-256 hashes of its components. Those hashes catch corruption, not
tampering: anyone who edits a component can rewrite the hashes in the manifest to match.
Signing closes that gap. A signature by a key you trust proves who produced the artifact and
that nothing in it changed since.

Signing uses Ed25519 and needs the `[sign]` extra:

[Run this example](#local-example-0).


## What is signed

The signature covers the exact bytes of the artifact's `manifest.json`. The manifest holds the
hash of every component and the content id, so the signature covers the whole artifact. It is
stored in the archive member `manifest.sig`, outside the hashed set. The signed message is a
fixed domain prefix followed by the manifest bytes, so the signature cannot be replayed as a
signature of anything else.

A profile that has a value vault (`docs/VAULT.md`) records `vault: {"vault_id", "sha256"}` in its
manifest, so the signature covers the vault's hash: replace or alter the vault and `shape vault
verify --verify PUBKEY` and `shape generate --vault ... --verify PUBKEY` fail (exit 1). Signing needs
no extra step for this; sign the profile after the vault is written (`shape profile --sign KEY`
does). `shape vault rekey` writes a new profile, drops the old signature (a notice says so) and
signs the new one only with `--key`.

`--verify` fails (exit code 1) when the artifact:

- is unsigned, or has had its signature stripped;
- was changed after signing, even if its hashes were rewritten to match;
- was signed by a different key than the one you trust.

Verification checks the signature first and then every content hash.

## Commands

[Run this example](#local-example-1).


`--verify PUBKEY` on `inspect`, `show`, `check`, `diff`, `query` and `plan` verifies every
`.shape` input before the command runs and stops with exit code 1 if one fails.

In Python:

```python
from shape.artifact import sign_artifact, verify_artifact, read_model
from shape.artifact.signing import generate_keypair, load_private_key, load_public_key

sign_artifact("data.shape", load_private_key("release.key", passphrase))
verify_artifact("data.shape", load_public_key("release.pub"))
manifest, model = read_model("data.shape", verify_key=load_public_key("release.pub"))
```

## Where keys come from

`--key` (`shape sign`), `--sign` (`profile`, `capture`) and `--verify` / `--key` (public keys)
take a *key source*:

| Source | Meaning |
| --- | --- |
| `PATH` | a key file; a **private** key file follows the `file://` rule below (refused on POSIX when group or others can access it) |
| `-` | standard input |
| `env://NAME` | the environment variable `NAME` (the key text itself) |
| `file://PATH` | a file, read like a credential: refused on POSIX when group or others can access it (`chmod 600`); a **public** key may be world-readable |
| `kv://...` | a secret store, through a resolver the host registers (the Fabric plugin provides Azure Key Vault) |

So a pipeline never has to write the key to disk:

[Run this example](#local-example-3).


The `env://`, `file://` and `kv://` references are the same credential references that Fabric
credentials use. `kv://` is pluggable because core ships no cloud SDK: register a resolver with
`shape.security.credrefs.register_resolver("kv", fn)` (`fn` takes the text after `kv://` and
returns the secret). Without one, `kv://` fails with a message saying so. Reference errors name
the variable or path, never a value. The full description of the references, the `file://`
permission rule and the sign-in modes of the Fabric destinations is in
[`docs/plugins/fabric-auth.md`](plugins/fabric-auth.md).

## Encrypted private keys

`shape keygen` protects the private key with a passphrase by default. The file is a standard
PKCS#8 PEM (`ENCRYPTED PRIVATE KEY`) that OpenSSL and other tools read. The passphrase comes from,
in order: `--passphrase-env VAR`, `--passphrase-stdin` (first line), `SHAPE_KEY_PASSPHRASE`, or a
prompt when attached to a terminal (asked twice when creating a key). **There is deliberately no
option that takes the passphrase as an argument**: a command line shows up in process listings and
shell history. Signing with an encrypted key asks for the passphrase the same way, only when the
key is encrypted. The key and the passphrase cannot both come from standard input.

`shape keygen PREFIX --no-passphrase` writes the old raw form (32 bytes, base64) and prints a
warning. Raw keys are still read. Python: `write_keypair(prefix, passphrase)`, or
`write_keypair(prefix, unencrypted=True)`, which also warns (`UnencryptedKeyWarning`).

The private key file is created with mode `0600`. Windows has no mode bits: keygen says so
(`KeyFilePermissionWarning`); restrict the file with its ACL and keep the key encrypted.

## Reading an artifact whose signature was not checked

A signature is only checked when you give the reader a trusted key (`--verify`, `verify_key=`).
A plain read does not check it, so it says so. Whenever an artifact is read without a trusted key:

- the CLI prints one line to stderr, for example
  `shape: note: data.shape is signed by key 912d..., but the signature was not verified: no trusted key was given (check it with --verify PUBKEY)`,
  or `... is not signed: its origin is not verified ...`;
- the Python readers (`read_artifact`, `read_model`, `read_shape`) return a tuple that also has
  `.signature`: `{"status": "unsigned" | "signed_not_verified" | "verified", "verified": bool,
  "key_id": ...}`, and raise an `ArtifactNotVerifiedWarning` (route it with
  `shape.artifact.io.set_notice_handler`, or pass `notice=False` to silence one read);
- `shape inspect` includes the same `signature` object in its JSON.

`shape cat` takes `--verify PUBKEY` too. As a git `textconv` filter it reads a temporary copy of
each file, so it prints the note on every diff of an unsigned or unchecked `.shape`; put the key in
the filter (`shape git-setup --command 'shape cat --verify PUBKEY'`) to check each file and print
no note, or to refuse to show one that is not signed by that key.

The notice never changes what is accepted: an invalid, forged or wrong-key signature still fails
closed with exit code 1 under `--verify`, and a plain read stays a plain read.

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

## Key handling

- **Key files** are an encrypted PKCS#8 PEM (default) or the raw 32-byte Ed25519 key,
  base64-encoded, on one line. `shape keygen` creates the private key with mode `0600` where the
  OS supports it and refuses to overwrite an existing file.
- **Keep the private key secret.** Anyone who holds it can sign any content as you. Do not
  commit it, put it in an image, or pass it on a command line. In CI, supply it from a secret
  store through `env://` or `-` instead of a file, and its passphrase through a separate secret.
  An environment variable is visible to the same user's other processes: it is better than a
  file left on disk, not as good as a store that never exposes the key to the job.
  Shape never prints a key or a passphrase.
- **Distribute the public key out of band**, not next to the artifacts it verifies. An
  attacker who can replace an artifact can also replace a public key shipped beside it.
  Compare the key id (`key_id` in `keygen` and `verify` output, the first 16 hex digits of the
  key's SHA-256) through a second channel.
- **Pin the key you trust.** `--verify` takes one public key and accepts only signatures made
  by it. The key id inside the signature is a hint for error messages, not a trust anchor.
- **Rotation.** Generate a new pair and sign with it from then on. Artifacts signed before the
  rotation stay valid under the old key: keep its *public* half in your trust list for as long as
  those artifacts matter (a signature names its `key_id`, and nothing about it expires), and
  never sign with the retired private key again. To move an artifact you still publish to the new
  key, re-sign it (`shape sign` replaces an earlier signature) and keep the original file if you
  need the evidence. Verifiers keep trusting a key until they are given a different one, so a
  verifier that gets only the new key rejects old artifacts with "signed by a different key":
  give it both. `--verify` takes one key per call; verify old and new artifacts with their own.
- **Compromise.** If a private key leaks, stop trusting its public key, generate a new pair
  and re-sign. Artifacts signed before the leak cannot be told apart from forgeries made
  with the stolen key, so re-sign from a source you trust.
- Signing says nothing about whether the content is safe to share. Classification and secret
  scanning still apply (see `SECURITY_SPECIFICATION.md`).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
pip install --quiet --no-deps --no-build-isolation -e "$SHAPE_DOCS_REPO"
```

??? info "Output (exit 0)"

    ```text {.expected}
    (no output)
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape keygen release              # writes release.key (encrypted, mode 0600) and release.pub
shape profile data.csv -o data.shape --sign release.key
shape capture data.csv -o data.shape --sign release.key
shape sign data.shape --key release.key        # sign an existing artifact (-o OUT to keep the original)
shape inspect data.shape --verify release.pub  # also check, diff, query, plan
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"encrypted": true, "key_id": "d03e8cd9841b3fe6", "private_key": "release.key", "public_key": "release.pub"}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: data.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "9865171cc49d7241aa6d994f131a40e7992a79fe1ebb537dbdd52b7dedab1d9a", "signed_by": "d03e8cd9841b3fe6", "written": "data.shape"}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: data.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "47e391816f192210bfa00f6f99d7c615c56814645610ce7f24841e9ec8f286ea", "signed_by": "d03e8cd9841b3fe6", "written": "data.shape"}
    {"key_id": "d03e8cd9841b3fe6", "signed": "data.shape"}
    {"kind": "model", "manifest": {"classification": "PUBLIC", "content_hashes": {"shape.json": "47e391816f192210bfa00f6f99d7c615c56814645610ce7f24841e9ec8f286ea"}, "fidelity": "gold", "format": "shape", "format_version": 2, "metadata": {}, "min_shape_version": "0.9.0", "name": "data", "shape_content_id": "47e391816f192210bfa00f6f99d7c615c56814645610ce7f24841e9ec8f286ea", "shape_version": "0.9.1", "version": 2}, "shape": {"engine": "shape-capture-v1", "mode": "bounded", "name": "data", "schema_version": 2, "tables": {"data": {"columns": [{"arrow_type": "unknown", "count": 100, "distinct": 100.30607729281405, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "float", "max": 100, "mean": 50.5, "min": 1, "name": "order_id", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 25.0, "0.5": 50.0, "0.75": 75.0}, "top": [[65, 2, 1], [66, 2, 1], [67, 2, 1], [68, 2, 1], [69, 2, 1], [70, 2, 1], [71, 2, 1], [72, 2, 1], [73, 2, 1], [74, 2, 1]], "variance_population": 833.25}, {"arrow_type": "unknown", "count": 100, "distinct": 20.012410819218463, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "float", "max": 20, "mean": 10.5, "min": 1, "name": "customer_id", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 5.0, "0.5": 10.0, "0.75": 15.0}, "top": [[1, 5, 0], [2, 5, 0], [3, 5, 0], [4, 5, 0], [5, 5, 0], [6, 5, 0], [7, 5, 0], [8, 5, 0], [9, 5, 0], [10, 5, 0]], "variance_population": 33.25}, {"arrow_type": "unknown", "count": 100, "distinct": 20.0124087832318, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 21, "mean": 20.5, "min": 20, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 20.0, "q50": 20.5, "q75": 21.0, "topk": [[20, 50, 0], [21, 50, 0]], "variance_population": 0.25}, "name": "customer_email", "null_count": 0, "top": [["person0@example.test", 5, 0], ["person10@example.test", 5, 0], ["person11@example.test", 5, 0], ["person12@example.test", 5, 0], ["person13@example.test", 5, 0], ["person14@example.test", 5, 0], ["person15@example.test", 5, 0], ["person16@example.test", 5, 0], ["person17@example.test", 5, 0], ["person18@example.test", 5, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241679541289, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 4, "mean": 4.0, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.0, "q75": 4.0, "topk": [[4, 100, 0]], "variance_population": 0.0}, "name": "status", "null_count": 0, "top": [["paid", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 99.30019313866805, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "float", "max": 67.1, "mean": 38.8355, "min": 10.571, "name": "amount", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 24.275, "0.5": 38.55, "0.75": 52.825}, "top": [[47.115, 2, 1], [47.686, 2, 1], [48.257, 2, 1], [48.828, 2, 1], [49.399, 2, 1], [49.97, 2, 1], [50.541, 2, 1], [51.112, 2, 1], [51.683, 2, 1], [52.254, 2, 1]], "variance_population": 271.67366325}, {"arrow_type": "unknown", "count": 100, "distinct": 99.30019313866805, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "float", "max": 67.1, "mean": 38.8355, "min": 10.571, "name": "order_total", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 24.275, "0.5": 38.55, "0.75": 52.825}, "top": [[47.115, 2, 1], [47.686, 2, 1], [48.257, 2, 1], [48.828, 2, 1], [49.399, 2, 1], [49.97, 2, 1], [50.541, 2, 1], [51.112, 2, 1], [51.683, 2, 1], [52.254, 2, 1]], "variance_population": 271.67366325}, {"arrow_type": "unknown", "count": 100, "distinct": 28.023688559894886, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "name": "placed_at", "null_count": 0, "top": [["2026-09-02T12:00:00", 4, 0], ["2026-09-03T12:00:00", 4, 0], ["2026-09-04T12:00:00", 4, 0], ["2026-09-05T12:00:00", 4, 0], ["2026-09-06T12:00:00", 4, 0], ["2026-09-07T12:00:00", 4, 0], ["2026-09-08T12:00:00", 4, 0], ["2026-09-09T12:00:00", 4, 0], ["2026-09-10T12:00:00", 4, 0], ["2026-09-11T12:00:00", 4, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 28.02369527715505, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "name": "shipped_at", "null_count": 0, "top": [["2026-09-02T13:00:00", 4, 0], ["2026-09-03T13:00:00", 4, 0], ["2026-09-04T13:00:00", 4, 0], ["2026-09-05T13:00:00", 4, 0], ["2026-09-06T13:00:00", 4, 0], ["2026-09-07T13:00:00", 4, 0], ["2026-09-08T13:00:00", 4, 0], ["2026-09-09T13:00:00", 4, 0], ["2026-09-10T13:00:00", 4, 0], ["2026-09-11T13:00:00", 4, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 28.023688559894886, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "name": "order_date", "null_count": 0, "top": [["2026-09-02T12:00:00", 4, 0], ["2026-09-03T12:00:00", 4, 0], ["2026-09-04T12:00:00", 4, 0], ["2026-09-05T12:00:00", 4, 0], ["2026-09-06T12:00:00", 4, 0], ["2026-09-07T12:00:00", 4, 0], ["2026-09-08T12:00:00", 4, 0], ["2026-09-09T12:00:00", 4, 0], ["2026-09-10T12:00:00", 4, 0], ["2026-09-11T12:00:00", 4, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241679541289, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 4, "mean": 4.0, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.0, "q75": 4.0, "topk": [[4, 100, 0]], "variance_population": 0.0}, "name": "discount_code", "null_count": 0, "top": [["SAVE", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 2.000109384901645, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 5, "mean": 4.9, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 90, 0], [4, 10, 0]], "variance_population": 0.09}, "name": "is_gift", "null_count": 0, "top": [["False", 90, 0], ["True", 10, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 2.000109379736731, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 100, 0]], "variance_population": 0.0}, "name": "region", "null_count": 0, "top": [["north", 50, 0], ["south", 50, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241685997038, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 100, 0]], "variance_population": 0.0}, "name": "tier", "null_count": 0, "top": [["standard", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 2.000109384901645, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 5, "mean": 4.5, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.5, "q75": 5.0, "topk": [[4, 50, 0], [5, 50, 0]], "variance_population": 0.25}, "name": "churned", "null_count": 0, "top": [["False", 50, 0], ["True", 50, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241666629792, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 100, 0]], "variance_population": 0.0}, "name": "zip", "null_count": 0, "top": [["10001", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241666629792, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 100, 0]], "variance_population": 0.0}, "name": "city", "null_count": 0, "top": [["New York", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241666629792, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 2, "mean": 2.0, "min": 2, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 2.0, "q50": 2.0, "q75": 2.0, "topk": [[2, 100, 0]], "variance_population": 0.0}, "name": "state", "null_count": 0, "top": [["NY", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241679541289, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 2, "mean": 2.0, "min": 2, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 2.0, "q50": 2.0, "q75": 2.0, "topk": [[2, 100, 0]], "variance_population": 0.0}, "name": "country", "null_count": 0, "top": [["US", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241666629792, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 22, "mean": 22.0, "min": 22, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 22.0, "q50": 22.0, "q75": 22.0, "topk": [[22, 100, 0]], "variance_population": 0.0}, "name": "iban", "null_count": 0, "top": [["GB82WEST12345698765432", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 1.0000241679541289, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 17, "mean": 17.0, "min": 17, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 17.0, "q50": 17.0, "q75": 17.0, "topk": [[17, 100, 0]], "variance_population": 0.0}, "name": "notes", "null_count": 0, "top": [["synthetic example", 100, 0]]}, {"arrow_type": "unknown", "count": 100, "distinct": 100.30621526130676, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 11, "mean": 11.0, "min": 11, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 11.0, "q50": 11.0, "q75": 11.0, "topk": [[11, 100, 0]], "variance_population": 0.0}, "name": "token", "null_count": 0, "top": [["222-11-0065", 2, 1], ["222-11-0066", 2, 1], ["222-11-0067", 2, 1], ["222-11-0068", 2, 1], ["222-11-0069", 2, 1], ["222-11-0070", 2, 1], ["222-11-0071", 2, 1], ["222-11-0072", 2, 1], ["222-11-0073", 2, 1], ["222-11-0074", 2, 1]]}, {"arrow_type": "unknown", "count": 100, "distinct": 100.30621526130676, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 11, "mean": 11.0, "min": 11, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 11.0, "q50": 11.0, "q75": 11.0, "topk": [[11, 100, 0]], "variance_population": 0.0}, "name": "ssn", "null_count": 0, "top": [["222-11-0065", 2, 1], ["222-11-0066", 2, 1], ["222-11-0067", 2, 1], ["222-11-0068", 2, 1], ["222-11-0069", 2, 1], ["222-11-0070", 2, 1], ["222-11-0071", 2, 1], ["222-11-0072", 2, 1], ["222-11-0073", 2, 1], ["222-11-0074", 2, 1]]}, {"arrow_type": "unknown", "count": 100, "distinct": 100.30624164738022, "distinct_exact": false, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "float", "max": 50100, "mean": 50050.5, "min": 50001, "name": "salary", "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "quantiles": {"0.25": 50025.0, "0.5": 50050.0, "0.75": 50075.0}, "top": [[50065, 2, 1], [50066, 2, 1], [50067, 2, 1], [50068, 2, 1], [50069, 2, 1], [50070, 2, 1], [50071, 2, 1], [50072, 2, 1], [50073, 2, 1], [50074, 2, 1]], "variance_population": 833.25}], "name": "data", "rows": 100}}, "x_legacy": {"columns": {"amount": {"count": 100, "distinct_estimate": 99.30019313866805, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "numeric", "max": 67.1, "mean": 38.8355, "min": 10.571, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 24.275, "q50": 38.55, "q75": 52.825, "topk": [[47.115, 2, 1], [47.686, 2, 1], [48.257, 2, 1], [48.828, 2, 1], [49.399, 2, 1], [49.97, 2, 1], [50.541, 2, 1], [51.112, 2, 1], [51.683, 2, 1], [52.254, 2, 1]], "variance_population": 271.67366325}, "churned": {"count": 100, "distinct_estimate": 2.000109384901645, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 5, "mean": 4.5, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.5, "q75": 5.0, "topk": [[4, 50, 0], [5, 50, 0]], "variance_population": 0.25}, "null_count": 0, "topk": [["False", 50, 0], ["True", 50, 0]]}, "city": {"count": 100, "distinct_estimate": 1.0000241666629792, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["New York", 100, 0]]}, "country": {"count": 100, "distinct_estimate": 1.0000241679541289, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 2, "mean": 2.0, "min": 2, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 2.0, "q50": 2.0, "q75": 2.0, "topk": [[2, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["US", 100, 0]]}, "customer_email": {"count": 100, "distinct_estimate": 20.0124087832318, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 21, "mean": 20.5, "min": 20, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 20.0, "q50": 20.5, "q75": 21.0, "topk": [[20, 50, 0], [21, 50, 0]], "variance_population": 0.25}, "null_count": 0, "topk": [["person0@example.test", 5, 0], ["person10@example.test", 5, 0], ["person11@example.test", 5, 0], ["person12@example.test", 5, 0], ["person13@example.test", 5, 0], ["person14@example.test", 5, 0], ["person15@example.test", 5, 0], ["person16@example.test", 5, 0], ["person17@example.test", 5, 0], ["person18@example.test", 5, 0]]}, "customer_id": {"count": 100, "distinct_estimate": 20.012410819218463, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "numeric", "max": 20, "mean": 10.5, "min": 1, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 10.0, "q75": 15.0, "topk": [[1, 5, 0], [2, 5, 0], [3, 5, 0], [4, 5, 0], [5, 5, 0], [6, 5, 0], [7, 5, 0], [8, 5, 0], [9, 5, 0], [10, 5, 0]], "variance_population": 33.25}, "discount_code": {"count": 100, "distinct_estimate": 1.0000241679541289, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 4, "mean": 4.0, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.0, "q75": 4.0, "topk": [[4, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["SAVE", 100, 0]]}, "iban": {"count": 100, "distinct_estimate": 1.0000241666629792, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 22, "mean": 22.0, "min": 22, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 22.0, "q50": 22.0, "q75": 22.0, "topk": [[22, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["GB82WEST12345698765432", 100, 0]]}, "is_gift": {"count": 100, "distinct_estimate": 2.000109384901645, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 2.0, "finite_count": 100, "max": 5, "mean": 4.9, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 90, 0], [4, 10, 0]], "variance_population": 0.09}, "null_count": 0, "topk": [["False", 90, 0], ["True", 10, 0]]}, "notes": {"count": 100, "distinct_estimate": 1.0000241679541289, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 17, "mean": 17.0, "min": 17, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 17.0, "q50": 17.0, "q75": 17.0, "topk": [[17, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["synthetic example", 100, 0]]}, "order_date": {"count": 100, "distinct_estimate": 28.023688559894886, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["2026-09-02T12:00:00", 4, 0], ["2026-09-03T12:00:00", 4, 0], ["2026-09-04T12:00:00", 4, 0], ["2026-09-05T12:00:00", 4, 0], ["2026-09-06T12:00:00", 4, 0], ["2026-09-07T12:00:00", 4, 0], ["2026-09-08T12:00:00", 4, 0], ["2026-09-09T12:00:00", 4, 0], ["2026-09-10T12:00:00", 4, 0], ["2026-09-11T12:00:00", 4, 0]]}, "order_id": {"count": 100, "distinct_estimate": 100.30607729281405, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "numeric", "max": 100, "mean": 50.5, "min": 1, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 25.0, "q50": 50.0, "q75": 75.0, "topk": [[65, 2, 1], [66, 2, 1], [67, 2, 1], [68, 2, 1], [69, 2, 1], [70, 2, 1], [71, 2, 1], [72, 2, 1], [73, 2, 1], [74, 2, 1]], "variance_population": 833.25}, "order_total": {"count": 100, "distinct_estimate": 99.30019313866805, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "numeric", "max": 67.1, "mean": 38.8355, "min": 10.571, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 24.275, "q50": 38.55, "q75": 52.825, "topk": [[47.115, 2, 1], [47.686, 2, 1], [48.257, 2, 1], [48.828, 2, 1], [49.399, 2, 1], [49.97, 2, 1], [50.541, 2, 1], [51.112, 2, 1], [51.683, 2, 1], [52.254, 2, 1]], "variance_population": 271.67366325}, "placed_at": {"count": 100, "distinct_estimate": 28.023688559894886, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["2026-09-02T12:00:00", 4, 0], ["2026-09-03T12:00:00", 4, 0], ["2026-09-04T12:00:00", 4, 0], ["2026-09-05T12:00:00", 4, 0], ["2026-09-06T12:00:00", 4, 0], ["2026-09-07T12:00:00", 4, 0], ["2026-09-08T12:00:00", 4, 0], ["2026-09-09T12:00:00", 4, 0], ["2026-09-10T12:00:00", 4, 0], ["2026-09-11T12:00:00", 4, 0]]}, "region": {"count": 100, "distinct_estimate": 2.000109379736731, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["north", 50, 0], ["south", 50, 0]]}, "salary": {"count": 100, "distinct_estimate": 100.30624164738022, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}, "quantiles": {"algorithm": "kll-v2", "confidence": 0.99, "exact": false, "parameters": {"error": "rank", "k": 200}, "relative_error": 0.01}}, "finite_count": 100, "kind": "numeric", "max": 50100, "mean": 50050.5, "min": 50001, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 50025.0, "q50": 50050.0, "q75": 50075.0, "topk": [[50065, 2, 1], [50066, 2, 1], [50067, 2, 1], [50068, 2, 1], [50069, 2, 1], [50070, 2, 1], [50071, 2, 1], [50072, 2, 1], [50073, 2, 1], [50074, 2, 1]], "variance_population": 833.25}, "shipped_at": {"count": 100, "distinct_estimate": 28.02369527715505, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 19, "mean": 19.0, "min": 19, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 19.0, "q50": 19.0, "q75": 19.0, "topk": [[19, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["2026-09-02T13:00:00", 4, 0], ["2026-09-03T13:00:00", 4, 0], ["2026-09-04T13:00:00", 4, 0], ["2026-09-05T13:00:00", 4, 0], ["2026-09-06T13:00:00", 4, 0], ["2026-09-07T13:00:00", 4, 0], ["2026-09-08T13:00:00", 4, 0], ["2026-09-09T13:00:00", 4, 0], ["2026-09-10T13:00:00", 4, 0], ["2026-09-11T13:00:00", 4, 0]]}, "ssn": {"count": 100, "distinct_estimate": 100.30621526130676, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 11, "mean": 11.0, "min": 11, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 11.0, "q50": 11.0, "q75": 11.0, "topk": [[11, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["222-11-0065", 2, 1], ["222-11-0066", 2, 1], ["222-11-0067", 2, 1], ["222-11-0068", 2, 1], ["222-11-0069", 2, 1], ["222-11-0070", 2, 1], ["222-11-0071", 2, 1], ["222-11-0072", 2, 1], ["222-11-0073", 2, 1], ["222-11-0074", 2, 1]]}, "state": {"count": 100, "distinct_estimate": 1.0000241666629792, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 2, "mean": 2.0, "min": 2, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 2.0, "q50": 2.0, "q75": 2.0, "topk": [[2, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["NY", 100, 0]]}, "status": {"count": 100, "distinct_estimate": 1.0000241679541289, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 4, "mean": 4.0, "min": 4, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 4.0, "q50": 4.0, "q75": 4.0, "topk": [[4, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["paid", 100, 0]]}, "tier": {"count": 100, "distinct_estimate": 1.0000241685997038, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 8, "mean": 8.0, "min": 8, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 8.0, "q50": 8.0, "q75": 8.0, "topk": [[8, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["standard", 100, 0]]}, "token": {"count": 100, "distinct_estimate": 100.30621526130676, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 11, "mean": 11.0, "min": 11, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 11.0, "q50": 11.0, "q75": 11.0, "topk": [[11, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["222-11-0065", 2, 1], ["222-11-0066", 2, 1], ["222-11-0067", 2, 1], ["222-11-0068", 2, 1], ["222-11-0069", 2, 1], ["222-11-0070", 2, 1], ["222-11-0071", 2, 1], ["222-11-0072", 2, 1], ["222-11-0073", 2, 1], ["222-11-0074", 2, 1]]}, "zip": {"count": 100, "distinct_estimate": 1.0000241666629792, "error_models": {"cardinality": {"algorithm": "hyperloglog", "confidence": 0.95, "exact": false, "parameters": {"estimator": "ertl-improved", "hash": "xxh3-64", "p": 14, "registers": 16384}, "relative_error": 0.008125}}, "kind": "text", "length": {"count": 100, "distinct_estimate": 1.0, "finite_count": 100, "max": 5, "mean": 5.0, "min": 5, "nan_count": 0, "neg_inf_count": 0, "null_count": 0, "pos_inf_count": 0, "q25": 5.0, "q50": 5.0, "q75": 5.0, "topk": [[5, 100, 0]], "variance_population": 0.0}, "null_count": 0, "topk": [["10001", 100, 0]]}}, "rows": 100}}, "signature": {"key_id": "d03e8cd9841b3fe6", "status": "signed_not_verified", "verified": false}}
    ```

<a id="local-example-3"></a>

### Example 4

<!-- example: 3 -->

```bash {.runnable-reference}
export SHAPE_SIGNING_KEY=$(python - <<'PYKEY'
import base64, os
from shape.artifact.signing import load_private_key
print(base64.b64encode(load_private_key('release.key', os.environ['SHAPE_KEY_PASSPHRASE'])).decode())
PYKEY
)
shape sign data.shape --key env://SHAPE_SIGNING_KEY --passphrase-env SHAPE_SIGNING_PASSPHRASE
printf '%s' "$(cat release.key)" | shape sign data.shape --key - --passphrase-env SHAPE_SIGNING_PASSPHRASE
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"key_id": "d03e8cd9841b3fe6", "signed": "data.shape"}
    {"key_id": "d03e8cd9841b3fe6", "signed": "data.shape"}
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape migrate old.shape new-signed.shape --verify old.pub --sign-key release.key
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"destination": null, "dry_run": false, "plan": {"kind": "artifact", "noop": true, "result_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "source_content_id": "7cf1c15de54d60a4a786a42ae7c675fca759ed3a8b350fc214d07d0c5e959b78", "source_version": 2, "steps": [], "target_version": 2}, "receipt": null, "written": false}
    ```
