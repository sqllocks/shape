"""``shape migrate SRC DST`` (also installed as ``shape-migrate``): the offline migration of a
persisted file to the current form. See ``shape.migrate`` and
``docs/specs/STATE_AND_COMPATIBILITY.md``.

Exit codes: 0 migrated (or nothing to do, or a dry run), 1 the source's signature failed
``--verify``, 2 refused or bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shape migrate",
        description="Write a migrated copy of a persisted Shape file (a .shape artifact, a safe "
        "profile, a model, a run manifest, a contract, ...) in the current form. The original is "
        "kept, an existing destination is never overwritten, and a receipt "
        "(DST.receipt.json) records what was done.",
    )
    p.add_argument("src", metavar="SRC")
    p.add_argument("dst", metavar="DST", help="the new file (must not exist)")
    p.add_argument("--to", type=int, metavar="N", help="target version (default: the current one)")
    p.add_argument(
        "--kind",
        metavar="KIND",
        help="the file kind, for a JSON file that is not recognised by itself",
    )
    p.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    p.add_argument(
        "--verify",
        metavar="PUBKEY",
        help="check the source's signature against this public key first",
    )
    p.add_argument(
        "--sign-key",
        metavar="KEY",
        help="sign the migrated artifact and the receipt; a signed source needs this",
    )
    p.add_argument(
        "--unsigned-receipt",
        action="store_true",
        help="accept an unsigned receipt for a signed source",
    )
    p.add_argument("--passphrase-env", metavar="VAR", help="passphrase of an encrypted --sign-key")
    p.add_argument(
        "--passphrase-stdin", action="store_true", help="read that passphrase from stdin"
    )
    from shape.cli import exitcodes

    exitcodes.apply_to(p, "migrate")  # the exit-code table ends the help, as for every command
    return p


def run(a: argparse.Namespace) -> int:
    from shape import migrate

    verify_key = None
    sign_key = None
    if a.verify:
        from shape.artifact.signing import load_public_key

        verify_key = load_public_key(a.verify)
    if a.sign_key:
        from shape.artifact.keys import STDIN, read_passphrase
        from shape.artifact.signing import load_private_key

        if a.passphrase_stdin and a.sign_key == STDIN:
            raise ValueError("the key and the passphrase cannot both come from standard input")

        def passphrase() -> bytes | None:
            return read_passphrase(
                env=a.passphrase_env,
                use_stdin=a.passphrase_stdin,
                prompt="Private key passphrase: ",
            )

        sign_key = load_private_key(a.sign_key, passphrase)
    result = migrate.migrate_file(
        a.src,
        a.dst,
        to=a.to,
        kind=a.kind,
        dry_run=a.dry_run,
        sign_key=sign_key,
        verify_key=verify_key,
        unsigned_receipt=a.unsigned_receipt,
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0


def _run(a: argparse.Namespace) -> int:
    from shape.artifact.io import ArtifactSignatureError
    from shape.cli import errors

    try:
        return run(a)
    except ArtifactSignatureError as exc:
        if errors.debug_enabled():
            raise
        print(f"shape: signature check failed: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    a = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    from shape.cli import errors

    return errors.guarded(lambda: _run(a))


def main_entry() -> None:  # pragma: no cover - the console script
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    main_entry()
