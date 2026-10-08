"""``shape vault keygen|inspect|verify|rekey`` (W5-03).

The value vault is a separate encrypted file (``docs/VAULT.md``). Every command that needs a key
takes ``--kek REF`` (``env://NAME``, ``file://PATH``, a path or ``kv://...``), never the key itself.
No command prints, logs or puts a key or a decrypted value in a message.

Exit codes: 0 ok; 1 a check failed (the wrong key, a vault that is not the profile's, a hash, id,
signature or authentication mismatch); 2 bad input (a malformed vault, a newer ``version`` of it,
an unusable key or policy, an unreadable file). ``--json`` prints one JSON object on standard
output.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

COMMANDS = ("keygen", "inspect", "verify", "rekey")


def _kek_arg(p: argparse.ArgumentParser, name: str = "--kek", required: bool = False) -> None:
    p.add_argument(
        name,
        required=required,
        metavar="REF",
        help="key-encryption key: env://NAME (base64 of 32 bytes), file://PATH or a path "
        "(a file other users can read is refused). Never the key itself",
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shape vault",
        description="The value vault: an encrypted file of the values a safe capture withheld.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser(
        "keygen", help="write a new key-encryption key (32 random bytes, base64, mode 0600)"
    )
    kg.add_argument("-o", "--output", required=True, metavar="KEK.key")
    kg.add_argument("--json", action="store_true", help="print JSON")
    ins = sub.add_parser("inspect", help="print a vault's header (needs no key)")
    ins.add_argument("vault", metavar="VAULT")
    ins.add_argument("--json", action="store_true", help="print JSON")
    ver = sub.add_parser("verify", help="check a vault against its profile")
    ver.add_argument("vault", metavar="VAULT")
    ver.add_argument("--shape", required=True, metavar="X.shape", help="the profile")
    _kek_arg(ver)
    ver.add_argument("--verify", metavar="PUBKEY", help="also check the profile's signature")
    ver.add_argument("--json", action="store_true", help="print JSON")
    rk = sub.add_parser(
        "rekey", help="re-encrypt a vault under a new key and write the profile that refers to it"
    )
    rk.add_argument("shape", metavar="X.shape")
    rk.add_argument("vault", metavar="VAULT")
    _kek_arg(rk, required=True)
    _kek_arg(rk, "--new-kek", required=True)
    rk.add_argument("--out-shape", required=True, metavar="X2.shape")
    rk.add_argument("--out-vault", required=True, metavar="V2.shapevault")
    rk.add_argument("--key", metavar="SIGNING_KEY", help="sign the new profile with this key")
    rk.add_argument("--passphrase-env", metavar="VAR", help="passphrase of an encrypted --key")
    rk.add_argument(
        "--passphrase-stdin", action="store_true", help="read that passphrase from stdin"
    )
    rk.add_argument("--dry-run", action="store_true", help="list the two writes, write nothing")
    rk.add_argument("--json", action="store_true", help="print JSON")
    from shape.cli import exitcodes

    exitcodes.apply_to(p, "vault")  # the exit-code table ends the help, as for every command
    return p


def _out(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def _keygen(a: argparse.Namespace) -> int:
    from shape.vault.kek import write_kek
    from shape.vault.ops import ensure_git_ignored

    ensure_git_ignored([(a.output, "key-encryption key file")])
    key_id = write_kek(a.output)
    if a.json:
        _out({"ok": True, "path": a.output, "kek_id": key_id})
    else:
        print(f"wrote {a.output} (key id {key_id}); keep it out of version control and back it up")
    return 0


def _inspect(a: argparse.Namespace) -> int:
    from shape.vault.format import inspect_vault
    from shape.vault.ops import read_vault_bytes

    info = inspect_vault(read_vault_bytes(a.vault))
    if a.json:
        _out({"ok": True, **info})
        return 0
    print(f"vault {info['vault_id']}  (shape-vault v{info['version']}, {info['algorithm']})")
    print(f"profile  {info['profile_content_id']}")
    print(f"key id   {info['kek_id']}")
    for c in info["columns"]:
        print(f"  {c['column']:<40} {c['policy']:<10} {c['ciphertext_bytes']} bytes")
    return 0


def _verify(a: argparse.Namespace) -> int:
    from shape.vault.kek import resolve_kek
    from shape.vault.ops import verify_vault

    kek = resolve_kek(a.kek) if a.kek else None
    verify_key = None
    if a.verify:
        from shape.artifact.signing import load_public_key

        verify_key = load_public_key(a.verify)
    report = verify_vault(a.vault, a.shape, kek=kek, verify_key=verify_key)
    if a.json:
        _out(report)
    else:
        for c in report["checks"]:
            print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['detail']}")
        print("valid" if report["ok"] else "INVALID")
    return 0 if report["ok"] else 1


def _rekey(a: argparse.Namespace) -> int:
    from shape.vault.kek import resolve_kek
    from shape.vault.ops import rekey

    old, new = resolve_kek(a.kek), resolve_kek(a.new_kek)
    signing_key = None
    if a.key:
        from shape.artifact.keys import STDIN, load_private_key, read_passphrase

        if a.passphrase_stdin and a.key == STDIN:
            raise ValueError("the key and the passphrase cannot both come from standard input")

        def passphrase() -> bytes | None:
            return read_passphrase(
                env=a.passphrase_env,
                use_stdin=a.passphrase_stdin,
                prompt="Private key passphrase: ",
            )

        signing_key = load_private_key(a.key, passphrase)
    if not a.dry_run:
        from shape.vault.ops import ensure_git_ignored

        ensure_git_ignored([(a.out_vault, "vault")])
    result = rekey(
        a.shape,
        a.vault,
        kek=old,
        new_kek=new,
        out_shape=a.out_shape,
        out_vault=a.out_vault,
        signing_key=signing_key,
        dry_run=a.dry_run,
    )
    for note in result["notices"]:
        print(f"shape: notice: {note}", file=sys.stderr)
    if a.json:
        _out({"ok": True, **result})
    elif a.dry_run:
        print("dry run: would write")
        for w in result["writes"]:
            print(f"  {w}")
    else:
        print(f"wrote {a.out_vault} and {a.out_shape}")
    return 0


_RUN = {"keygen": _keygen, "inspect": _inspect, "verify": _verify, "rekey": _rekey}


def run(a: argparse.Namespace) -> int:
    from shape.cli import errors
    from shape.vault.errors import VaultMismatchError

    try:
        return _RUN[a.cmd](a)
    except VaultMismatchError as exc:
        if errors.debug_enabled():
            raise
        print(f"shape: vault check failed: {errors.describe(exc)}", file=sys.stderr)
        return 1
    except Exception as exc:
        from shape.artifact.io import ArtifactSignatureError

        if isinstance(exc, ArtifactSignatureError):
            if errors.debug_enabled():
                raise
            print(f"shape: signature check failed: {exc}", file=sys.stderr)
            return 1
        raise


def main(argv: Sequence[str] | None = None) -> int:
    a = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from shape.cli import errors

    return errors.guarded(lambda: run(a))
