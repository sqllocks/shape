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
shape keygen release              # writes release.key (private, mode 0600) and release.pub
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

sign_artifact("data.shape", load_private_key("release.key"))
verify_artifact("data.shape", load_public_key("release.pub"))
manifest, model = read_model("data.shape", verify_key=load_public_key("release.pub"))
```

## Key handling

- **Key files** hold the 32-byte Ed25519 key, base64-encoded, on one line. `shape keygen`
  creates the private key with mode `0600` and refuses to overwrite an existing file.
- **Keep the private key secret.** Anyone who holds it can sign any content as you. Do not
  commit it, put it in an image, or pass it on a command line. In CI, write it from a secret
  store to a file readable only by the job and delete it afterwards.
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
