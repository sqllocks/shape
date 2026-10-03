"""``shape fingerprint embed|show|verify`` and the writing side of ``shape generate --fingerprint``
(W5-10, ``docs/FINGERPRINT.md``).

Exit codes: 0 valid, 1 a digest or signature mismatch, 2 no fingerprint, a newer ``version`` or bad
input. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_KEY_HELP = (
    "private key: a file, - (standard input), env://VAR, file://PATH or kv://... "
    "(an encrypted key asks for its passphrase)"
)


def add_arguments(sub: Any) -> None:
    from shape.cli.main import _add_passphrase_args

    top = sub.add_parser(
        "fingerprint",
        help="embed, show and verify the fingerprint of a Parquet file or Delta table",
        description="A fingerprint is a JSON document stored in the Parquet footer (key "
        "shape.fingerprint) or as the Delta table property shape.fingerprint. It says the data is "
        "synthetic, names the run and the table, and is signed with Ed25519 when you give a key "
        "(extra [sign]). See docs/FINGERPRINT.md.",
    )
    cmds = top.add_subparsers(dest="fingerprint_cmd", required=True)
    em = cmds.add_parser("embed", help="write the fingerprint of a run into a file or table")
    em.add_argument("target", metavar="FILE|DELTA_DIR")
    em.add_argument("--run", required=True, metavar="MANIFEST.json", help="the run manifest")
    em.add_argument("--profile", metavar="P.shape", help="the profile the run was generated from")
    em.add_argument("--key", metavar="PRIVATE_KEY", help="sign with this key. " + _KEY_HELP)
    _add_passphrase_args(em)  # type: ignore[no-untyped-call]
    sh = cmds.add_parser("show", help="print the fingerprint")
    sh.add_argument("target", metavar="FILE|DELTA_DIR")
    ve = cmds.add_parser(
        "verify",
        help="recompute table_id from the data; check it and the signature",
        description="Exit 0 valid, 1 digest or signature mismatch, 2 no fingerprint, a newer "
        "version or bad input.",
    )
    ve.add_argument("target", metavar="FILE|DELTA_DIR")
    ve.add_argument(
        "--public-key",
        metavar="KEY",
        help="check the signature against this public key (a file, or any key source); without "
        "it a signature is reported as not checked",
    )


def _table_name(target: Path, tables: dict[str, Any]) -> str:
    """The manifest table that ``target`` is: by file path, then by name (file stem, or the
    directory's name)."""
    for name, entry in tables.items():
        for fp in (entry or {}).get("file_paths", []) or []:
            if Path(str(fp)).name == target.name:
                return str(name)
    stem = target.name if target.is_dir() else target.stem
    if stem in tables:
        return stem
    raise ValueError(
        f"{target.name} is not a table of the run manifest (tables: {', '.join(sorted(tables))})"
    )


def _embed(a: argparse.Namespace) -> int:
    from shape import fingerprint
    from shape.scenario.manifest import ManifestBuilder

    target = Path(a.target)
    kind = fingerprint.kind_of(target)
    manifest = ManifestBuilder.from_file(a.run)
    if not manifest.dataset_id or not manifest.reproducibility:
        raise ValueError(
            f"{a.run} has no dataset_id or reproducibility tuple (a run made before Shape "
            "recorded them); run it again"
        )
    name = _table_name(target, manifest.tables)
    private_key = None
    if a.key:
        fingerprint._require_crypto()
        from shape.cli.main import _private_key

        private_key = _private_key(a.key, a)  # type: ignore[no-untyped-call]
    content_id = None
    if a.profile:
        from shape.artifact.io import read_artifact

        content_id = str(read_artifact(a.profile, notice=False)[0].get("shape_content_id") or "")
        if not content_id:
            raise ValueError(f"{a.profile} has no content id (is it a .shape profile?)")
    table = fingerprint.read_data(target)
    doc = fingerprint.build(
        name,
        table,
        dataset_id=manifest.dataset_id,
        reproducibility=manifest.reproducibility,
        profile_content_id=content_id,
        private_key=private_key,
    )
    fingerprint.embed_text(target, fingerprint.dump(doc))
    json.dump(
        {
            "target": str(target),
            "kind": kind,
            "table": name,
            "table_id": doc["table_id"],
            "dataset_id": doc["dataset_id"],
            "key_id": doc["key_id"],
            "signed": doc["signature"] is not None,
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


def run(a: argparse.Namespace) -> int:
    from shape import fingerprint

    cmd = a.fingerprint_cmd
    if cmd == "embed":
        return _embed(a)
    if cmd == "show":
        json.dump(fingerprint.read_fingerprint(a.target), sys.stdout, indent=2, sort_keys=True)
        print()
        return 0
    public_key = None
    if a.public_key:
        from shape.artifact.keys import load_public_key

        public_key = load_public_key(a.public_key)
    outcome = fingerprint.verify(a.target, public_key)
    stream = sys.stdout if outcome.ok else sys.stderr
    verdict = "valid" if outcome.ok else "NOT valid"
    print(f"{a.target}: fingerprint {verdict}", file=stream)
    for line in outcome.messages():
        print(f"  {line}", file=stream)
    return 0 if outcome.ok else 1


def generate_fingerprinted(engine: Any, a: argparse.Namespace) -> list[Path]:
    """``shape generate --fingerprint``: the tables are generated whole (their ``dataset_id`` is
    needed before the first file is written), then each is written with its fingerprint."""
    from shape.cli.generation import _sink_options
    from shape.generation.output import write_result
    from shape.repro import dataset_id, reproducibility_tuple

    result = engine.generate()
    context = {
        "dataset_id": dataset_id({n: result.tables[n] for n in result.generation_order}),
        "reproducibility": reproducibility_tuple(engine.seed, engine.schema.generation.scale),
    }
    return write_result(result, a.format, a.output, fingerprint=context, **_sink_options(a))
