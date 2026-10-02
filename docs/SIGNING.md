# Signing `.shape` artifacts

A `.shape` file carries SHA-256 hashes of its components. Those hashes catch corruption, not
tampering: anyone who edits a component can rewrite the hashes in the manifest to match.
Signing closes that gap. A signature by a key you trust proves who produced the artifact and
that nothing in it changed since.

Signing uses Ed25519 and needs the `[sign]` extra:

```bash
pip install "sqllocks-shape[sign]"
```

## What is signed

The signature covers the exact bytes of the artifact's `manifest.json`. The manifest holds the
hash of every component and the content id, so the signature covers the whole artifact. It is
stored in the archive member `manifest.sig`, outside the hashed set. The signed message is a
fixed domain prefix followed by the manifest bytes, so the signature cannot be replayed as a
signature of anything else.

`--verify` fails (exit code 1) when the artifact:

- is unsigned, or has had its signature stripped;
- was changed after signing, even if its hashes were rewritten to match;
- was signed by a different key than the one you trust.

Verification checks the signature first and then every content hash.

## Commands

```bash
shape keygen release              # writes release.key (encrypted, mode 0600) and release.pub
shape profile data.csv -o data.shape --sign release.key
shape capture data.csv -o data.shape --sign release.key
shape sign data.shape --key release.key        # sign an existing artifact (-o OUT to keep the original)
shape verify data.shape --key release.pub      # exit 0 valid, 1 not valid, 2 unreadable
shape inspect data.shape --verify release.pub  # also check, diff, query, plan
```

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
| `PATH` | a key file |
| `-` | standard input |
| `env://NAME` | the environment variable `NAME` (the key text itself) |
| `file://PATH` | a file, read like a credential |
| `kv://...` | a secret store, through a resolver the host registers |

So a pipeline never has to write the key to disk:

```bash
shape sign data.shape --key env://SHAPE_SIGNING_KEY --passphrase-env SHAPE_SIGNING_PASSPHRASE
printf '%s' "$KEY" | shape sign data.shape --key - --passphrase-env SHAPE_SIGNING_PASSPHRASE
```

The `env://`, `file://` and `kv://` references are the same credential references that Fabric
credentials use. `kv://` is pluggable because core ships no cloud SDK: register a resolver with
`shape.security.credrefs.register_resolver("kv", fn)` (`fn` takes the text after `kv://` and
returns the secret). Without one, `kv://` fails with a message saying so. Reference errors name
the variable or path, never a value.

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

The notice never changes what is accepted: an invalid, forged or wrong-key signature still fails
closed with exit code 1 under `--verify`, and a plain read stays a plain read.

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
- **Rotation.** Generate a new pair, re-sign the artifacts you still publish
  (`shape sign` replaces an earlier signature), and retire the old public key. Verifiers
  keep trusting a key until they are given a different one.
- **Compromise.** If a private key leaks, stop trusting its public key, generate a new pair
  and re-sign. Artifacts signed before the leak cannot be told apart from forgeries made
  with the stolen key, so re-sign from a source you trust.
- Signing says nothing about whether the content is safe to share. Classification and secret
  scanning still apply (see `SECURITY_SPECIFICATION.md`).
