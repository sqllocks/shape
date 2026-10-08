"""Serialize every RefEngine domain's schema to JSON (runs in the RefEngine venv).

    source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/dump_schema.py [domain ...]

For each domain and each schema mode (3nf, star) the ``RefEngineSchema`` returned by
``Domain._build_schema()`` is written to ``$BENCH_OUT_DIR/schemas/<domain>_<mode>.json``
(``dataclasses.asdict`` of the schema: model, tables, relationships, business rules,
generation config). The domain verifier reads tables, FKs and business rules from these
files, so it holds no per-domain logic. Nothing under ``$REFENGINE_ROOT`` is modified.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _refpkg  # noqa: E402
from paths import BENCH_OUT_DIR, REFENGINE_ROOT  # noqa: E402

MODES = ("3nf", "star")


def schemas_dir() -> Path:
    return BENCH_OUT_DIR / "schemas"


def schema_path(domain: str, mode: str) -> Path:
    return schemas_dir() / f"{domain}_{mode}.json"


def domain_names() -> list[str]:
    sys.path.insert(0, str(REFENGINE_ROOT))
    _get_domain_registry = _refpkg.mod("cli")._get_domain_registry

    return sorted(_get_domain_registry())


def dump(domain: str, mode: str) -> Path:
    sys.path.insert(0, str(REFENGINE_ROOT))
    import composites

    if composites.is_composite(domain):  # a composite has the one (3nf) layout
        dom = composites.baseline_domain(composites.spec_of(domain))
    else:
        _resolve_domain = _refpkg.mod("cli")._resolve_domain

        dom = _resolve_domain(domain, mode)
    schema = dom._build_schema()
    out = schema_path(domain, mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataclasses.asdict(schema), indent=1, default=str))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("domains", nargs="*", help="default: every RefEngine domain")
    args = ap.parse_args(argv)
    for d in args.domains or domain_names():
        for m in MODES:
            print(dump(d, m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
