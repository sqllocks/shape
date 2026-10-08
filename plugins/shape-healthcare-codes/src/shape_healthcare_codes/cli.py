"""``shape healthcare-codes``: build, list and verify the code sets.

::

    shape healthcare-codes list
    shape healthcare-codes fetch icd10cm ndc            # download, pin-check, build
    shape healthcare-codes fetch hcpcs2 --file zip=./october-2026-alpha-numeric-hcpcs-file.zip
    shape healthcare-codes byo cpt ./cpt.csv --map code=CPT --map long_desc=Descriptor
    shape healthcare-codes byo hcc_coefficients ./2027-initial-model-software.zip
    shape healthcare-codes notices ndc
    shape healthcare-codes verify

Nothing here runs at import time and nothing reaches the network except ``fetch``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SHAPE_API = "1.0"


class HealthcareCodesCommand:
    """``shape healthcare-codes ...``."""

    name = "healthcare-codes"
    help = "build, list and verify healthcare reference code sets (ICD-10, NDC, HCPCS, ...)"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="hc_cmd", required=True, metavar="ACTION")
        ls = sub.add_parser("list", help="every asset: mode, whether it is built, where")
        ls.add_argument("--json", action="store_true", help="machine-readable output")
        ls.set_defaults(run=_list)
        fe = sub.add_parser("fetch", help="download a free asset from its official source")
        fe.add_argument("assets", nargs="*", help="asset ids (see `list`); with --all, none")
        fe.add_argument("--all", action="store_true", help="every fetchable asset")
        fe.add_argument("--dir", type=Path, help="data directory (default: the user cache)")
        fe.add_argument("--file", action="append", default=[], metavar="KEY=PATH")
        fe.add_argument("--subset", action="store_true", help="ICD-10-CM: the starter subset only")
        fe.set_defaults(run=_fetch)
        by = sub.add_parser("byo", help="load a licensed file you supply")
        by.add_argument("system", help="a bring-your-own system id (see `list`)")
        by.add_argument("path", type=Path)
        by.add_argument("--format", default="auto", choices=["auto", "delimited", "claml", "zip"])
        by.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN")
        by.add_argument("--dir", type=Path)
        by.set_defaults(run=_byo)
        nt = sub.add_parser("notices", help="the licence record of an asset")
        nt.add_argument("asset", nargs="?")
        nt.set_defaults(run=_notices)
        vf = sub.add_parser("verify", help="check built files against their manifests")
        vf.add_argument("--dir", type=Path)
        vf.set_defaults(run=_verify)

    def run(self, args: Any) -> int:
        return int(args.run(args))


def _kv(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        k, sep, v = item.partition("=")
        if not sep or not k or not v:
            raise SystemExit(f"expected KEY=VALUE, got {item!r}")
        out[k] = v
    return out


def _list(args: Any) -> int:
    from shape_healthcare_codes import store
    from shape_healthcare_codes.builders import BUILDERS
    from shape_healthcare_codes.provenance import all_assets

    built = store.available()
    rows = []
    for a in all_assets().values():
        names = ["rxnorm", "rxnorm_ndc"] if a.id == "rxnorm" else [a.id]
        have = [n for n in names if n in built]
        rows.append(
            {
                "asset": a.id,
                "mode": a.mode,
                "fetchable": a.id in BUILDERS,
                "built": have[0] and built[have[0]] if have else None,
                "licence": a.licence,
                "verify": a.verify,
            }
        )
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            flag = " [VERIFY]" if r["verify"] else ""
            print(f"{r['asset']:15} {r['mode']:6} built={r['built'] or '-':8} {r['licence']}{flag}")
    return 0


def _fetch(args: Any) -> int:
    from shape_healthcare_codes.builders import BUILDERS, run

    wanted = sorted(BUILDERS) if args.all else list(args.assets)
    if not wanted:
        raise SystemExit("name an asset, or use --all; `list` shows them")
    files = {k: Path(v) for k, v in _kv(args.file).items()} or None
    if files and len(wanted) != 1:
        raise SystemExit("--file applies to one asset")
    for asset in wanted:
        if asset not in BUILDERS:
            raise SystemExit(
                f"{asset!r} cannot be fetched (bring-your-own: use `byo`); fetchable: "
                f"{', '.join(sorted(BUILDERS))}"
            )
        for path in run(asset, args.dir, from_files=files, subset=args.subset):
            print(f"built {path}")
    return 0


def _byo(args: Any) -> int:
    from shape_healthcare_codes.byo import ByoError, load_byo

    try:
        path = load_byo(
            args.system, args.path, fmt=args.format, columns=_kv(args.map), data_dir=args.dir
        )
    except (ByoError, OSError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"built {path}")
    return 0


def _notices(args: Any) -> int:
    from shape_healthcare_codes.provenance import all_assets

    assets = all_assets()
    if args.asset and args.asset not in assets:
        print(f"unknown asset {args.asset!r}; known: {', '.join(sorted(assets))}")
        return 2
    for a in [assets[args.asset]] if args.asset else assets.values():
        print(f"{a.title} [{a.mode}]{' [VERIFY]' if a.verify else ''}")
        print(f"  source:  {a.source_url}")
        print(f"  release: {a.release}")
        print(f"  licence: {a.licence}")
        print(f"  page:    {a.licence_url} (read {a.checked_on})")
        print(f'  quote:   "{a.licence_quote}"')
    return 0


def _verify(args: Any) -> int:
    from shape_healthcare_codes import store

    d = args.dir or store.user_dir()
    bad = 0
    for path in sorted(d.glob("*.arrow")) if d.is_dir() else []:
        meta = path.with_suffix(".json")
        if not meta.is_file():
            print(f"{path.name}: no manifest")
            bad += 1
            continue
        info = json.loads(meta.read_text(encoding="utf-8"))
        table = store.read_table(path.stem, d)
        ok = table.num_rows == info.get("rows") and path.stat().st_size == info.get("bytes")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        print(
            f"{path.name}: {'ok' if ok else 'MISMATCH'} rows={table.num_rows} "
            f"release={info.get('release', '?')} sha256={digest}"
        )
        bad += 0 if ok else 1
    return 1 if bad else 0
