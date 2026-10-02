# ISS-sign — signing: key sources, encrypted keys, not-verified notice (issue #38)

Branch `lane/ISS-sign` (from `build/main-plan`). No gate, tolerance, decision (D-xx/T-xx) or
spec rule was changed; nothing was skipped, xfailed or disabled. The baseline checkout was not
used. The issue was not commented on, labelled or closed. No PR.

## Reproduction on this branch (before any change)

| # | Point in the issue | Reproduced here? | Evidence |
| --- | --- | --- | --- |
| 1 | keys are file paths only | **Yes** | `shape sign --key env://K` -> `[Errno 2] No such file or directory: 'env:/K'`; `--key -` -> `No such file or directory: '-'`; no `--sign-key-*` option in `--help` |
| 2 | private key unencrypted, no passphrase | **Yes** | `keygen` wrote `k1.key` of 45 bytes (raw base64); mode 600 on Linux; no passphrase option |
| 3 | plain read of a signed/forged artifact is silent | **Yes** | `shape inspect s.shape` (signed) and `d.shape` (unsigned): exit 0, stderr empty |
| 4 | `keygen` on an existing file shows a raw OS error | **Yes** | `shape: error: [Errno 17] File exists: 'k1.key'` |
| - | "what works" (wrong key, unsigned `--verify`, forged manifest, tamper, no overwrite) | Yes, covered by the existing `tests/artifact/test_signing.py`, still green | |

## What changed

* **(a) Key sources.** `-` (stdin), `env://NAME`, `file://PATH`, `kv://...` and plain paths, for
  private keys (`shape sign --key`, `profile/capture --sign`) and public keys (`--verify`,
  `shape verify --key`). The reference module is `shape/security/credrefs.py`: `env://` and
  `file://` built in; `kv://` is a pluggable resolver (`register_resolver("kv", fn)`), with a clear
  error when none is registered. No cloud SDK is imported by core (a test checks `sys.modules`).
  Tested with a fake resolver. P6-07b can reuse `credrefs.resolve_reference`.
* **(b) Encrypted keys.** `keygen` writes a standard PKCS#8 `ENCRYPTED PRIVATE KEY` PEM
  (`cryptography`'s `BestAvailableEncryption`) by default; passphrase from `--passphrase-env VAR`,
  `--passphrase-stdin`, `SHAPE_KEY_PASSPHRASE` or a prompt (twice when creating). There is no
  argument form. `--no-passphrase` writes the legacy raw key with a loud stderr warning
  (`UnencryptedKeyWarning` in the API). Mode 0600 via `O_CREAT|O_EXCL` where the OS has mode bits;
  on Windows a `KeyFilePermissionWarning` says to use an ACL. Raw and unencrypted PEM keys still
  load; an encrypted key asks for the passphrase only when it is encrypted. Existing key files:
  friendly `refusing to overwrite the existing file ...` (point 4 of the issue).
* **(c) Notice.** `read_artifact` / `read_model` / `read_shape` return a tuple subclass that
  still unpacks as a pair and has `.signature` (`status` unsigned | signed_not_verified |
  verified, `verified`, `key_id`). Without a trusted key a read raises
  `ArtifactNotVerifiedWarning` (handler replaceable with `set_notice_handler`, `notice=False`
  per read); the CLI prints `shape: note: ...` once per message on stderr (not for inputs that
  `--verify` just checked), and `shape inspect` JSON carries `signature`. Invalid, forged,
  wrong-key and unsigned-with-`--verify` still fail closed exactly as before (tests).
  A `key_id` read from an untrusted signature file is shown only if it is plain hex.

## Behaviour changes to be aware of (not decisions in the plan)

* `shape keygen PREFIX` with no passphrase source and no terminal now exits 2 (it used to write
  an unencrypted key). `--no-passphrase` restores the old output. `write_keypair(prefix)` without a
  passphrase raises `ValueError`; the two existing tests that relied on the old default were
  changed to ask for the raw key explicitly (`--no-passphrase` / `unencrypted=True`), nothing
  else in them was touched.
* A plain read of an unsigned artifact now prints a stderr note. Anything that scrapes stderr
  of `shape inspect/check/diff/...` on a `.shape` will see it.
* Python `read_*` callers see an `ArtifactNotVerifiedWarning` for unsigned files; filter it or
  pass `notice=False` where that is intended.

## Spec check (`docs/specs/SHAPE_ARTIFACT_SPEC.md`)

Unchanged and tested: signing input is `DOMAIN + exact manifest bytes` (Ed25519, a test verifies
a signature produced through an encrypted key against that message); readers fail closed;
content ids untouched; SAC-01: a file signed with an encrypted key twice, and re-signed, is
byte-identical (`test_signed_container_is_byte_reproducible_with_an_encrypted_key`) and the
existing `tests/artifact/test_container_reproducible.py` passes. Nothing needed escalating.

## Secret hygiene

`tests/security/test_signing_secrets.py` runs ten CLI scenarios (success and every failure:
wrong/missing passphrase, bad key, unset env, kv without a resolver, stdin key) plus API errors,
warnings and exception chains, and fails if the passphrase, the base64/hex raw key, the PEM body
or an unencrypted key file's text appears in any output (it checks its own detector too). While
writing it, a real finding: a resolver's failing exception stayed in `__context__`; fixed by
raising outside the handler.

## Tests added

`tests/artifact/test_signing_keys.py` (31), `tests/security/test_signing_secrets.py` (4).


## Merge of `origin/lane/P7-04`

P7-04 touched `src/shape/artifact/io.py` (bounded manifest/signature reads), `signing.py`
(`RecursionError` in the signature parse), `cli/main.py` (`--verify` sniffs content, not the file
extension) and others. Merged with a merge commit (no rebase, no force-push). One textual
conflict in `cli/main.py` (both sides added helpers next to `_verify_inputs`): kept both.
One semantic conflict: P7-04's `test_verify_checks_artifacts_whatever_their_file_name` called
`write_keypair(prefix)` expecting the old unencrypted default; it now passes a passphrase (the
test only uses the public key; its assertions are unchanged). The nightly workflow, the fuzzer and
the rest of P7-04 came in unchanged.
