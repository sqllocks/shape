"""``shape share-bundle create|verify`` (W5-10, ``docs/SHARE_BUNDLE.md``).

``create`` exits 0 with the bundle written, 1 when a check fails (no bundle is written) and 2 for
bad input. ``verify`` exits 0 valid, 1 tampered or failing, 2 malformed. Nothing from the source is
printed: messages carry table and column names and counts. Nothing heavy loads at import (T-18).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any


def add_arguments(sub: Any) -> None:
    from shape.cli.main import _KEY_HELP, _add_passphrase_args

    top = sub.add_parser(
        "share-bundle",
        help="a zip of generated data with a signed attestation that the checks passed",
        description="create runs the memorization gate and a top-values check against the source "
        "and writes the bundle only if both pass; verify recomputes the dataset id of the bundled "
        "data and checks the attestation. Evidence that the listed checks passed, not a privacy "
        "guarantee (docs/SHARE_BUNDLE.md).",
    )
    cmds = top.add_subparsers(dest="bundle_cmd", required=True)
    cr = cmds.add_parser("create", help="check the data against its source and write the bundle")
    cr.add_argument("data_dir", metavar="DATA_DIR", help="the generated tables (csv/parquet/jsonl)")
    cr.add_argument("--source", required=True, metavar="SOURCE_DIR", help="the real tables")
    cr.add_argument(
        "--classifications",
        required=True,
        metavar="CLASSES.json",
        help='{"table.column": "CONFIDENTIAL", ...}; columns at CONFIDENTIAL or above are checked',
    )
    cr.add_argument("-o", "--output", required=True, metavar="BUNDLE.zip")
    cr.add_argument("--key", metavar="PRIVATE_KEY", help="sign with this key. " + _KEY_HELP)
    cr.add_argument(
        "--top-k",
        type=int,
        default=20,
        metavar="K",
        help="no value among the source's K most frequent values of a restricted column may "
        "appear in the generated column (default 20)",
    )
    _add_passphrase_args(cr)  # type: ignore[no-untyped-call]
    ve = cmds.add_parser(
        "verify",
        help="recompute the dataset id and check the attestation",
        description="Exit 0 valid, 1 tampered or a check failed, 2 malformed bundle.",
    )
    ve.add_argument("bundle", metavar="BUNDLE.zip")
    ve.add_argument(
        "--public-key",
        metavar="KEY",
        help="check the signature against this public key; without it a signature is not checked",
    )


def run(a: argparse.Namespace) -> int:
    from shape import share_bundle

    if a.bundle_cmd == "verify":
        public_key = None
        if a.public_key:
            from shape.artifact.keys import load_public_key

            public_key = load_public_key(a.public_key)
        result = share_bundle.verify(a.bundle, public_key)
        stream = sys.stdout if result.ok else sys.stderr
        print(f"{a.bundle}: {'valid' if result.ok else 'NOT valid'}", file=stream)
        for line in result.lines:
            print(f"  {line}", file=stream)
        return 0 if result.ok else 1
    private_key = None
    if a.key:
        from shape.fingerprint import _require_crypto

        _require_crypto()
        from shape.cli.main import _private_key

        private_key = _private_key(a.key, a)  # type: ignore[no-untyped-call]
    result_c = share_bundle.create(
        a.data_dir,
        a.source,
        a.classifications,
        a.output,
        private_key=private_key,
        top_k=a.top_k,
    )
    if not result_c.ok:
        print("shape: no bundle written: a check failed", file=sys.stderr)
        for line in result_c.problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    att = result_c.attestation
    print(
        f"Wrote {a.output}: dataset_id {att['dataset_id']}, "
        f"{len(att['tables'])} tables, {len(att['checks'])} checks passed, "
        f"{'signed' if att['signature'] else 'not signed'}"
    )
    return 0
