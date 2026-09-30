"""Serialize every Spindle domain's schema to JSON (runs in the Spindle venv).

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/dump_schema.py [domain ...]

For each domain and each schema mode (3nf, star) the ``SpindleSchema`` returned by
``Domain._build_schema()`` is written to ``$BENCH_OUT_DIR/schemas/<domain>_<mode>.json``
(``dataclasses.asdict`` of the schema: model, tables, relationships, business rules,
generation config). The domain verifier reads tables, FKs and business rules from these
files, so it holds no per-domain logic. Nothing under ``$SPINDLE_ROOT`` is modified.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import BENCH_OUT_DIR, SPINDLE_ROOT  # noqa: E402

MODES = ("3nf", "star")


def schemas_dir() -> Path:
    return BENCH_OUT_DIR / "schemas"


def schema_path(domain: str, mode: str) -> Path:
    return schemas_dir() / f"{domain}_{mode}.json"


def domain_names() -> list[str]:
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.cli import _get_domain_registry

    return sorted(_get_domain_registry())


def dump(domain: str, mode: str) -> Path:
    sys.path.insert(0, str(SPINDLE_ROOT))
    from sqllocks_spindle.cli import _resolve_domain

    dom = _resolve_domain(domain, mode)
    schema = dom._build_schema()
    out = schema_path(domain, mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(schema), indent=1, default=str))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("domains", nargs="*", help="default: every Spindle domain")
    args = ap.parse_args(argv)
    for d in args.domains or domain_names():
        for m in MODES:
            print(dump(d, m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
