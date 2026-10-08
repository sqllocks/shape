"""The baseline's schemas and generation plans, as committed fixtures (runs in the RefEngine venv).

    source scripts/env.sh
    "$REFENGINE_PY" benchmarks/vs_refengine/plan_fixtures.py          # write
    "$REFENGINE_PY" benchmarks/vs_refengine/plan_fixtures.py --check  # compare

Writes, under ``fixtures/``:

* ``schemas/<domain>_<mode>.json``: the baseline's own schema of each of its 14 domains in each
  mode (the ``dump_schema.py`` output). ``schema_import.py`` imports these (P4-01a).
* ``composites/<id>.json``: the same for the baseline's composites (``composites.py``): the merged
  schema and its plan, one file per preset and ad-hoc combination (P6-01e).
* ``plan.json``: for each of those schemas, what the baseline's engine plans, for the Shape
  engine (P4-02) to equal: ``row_counts`` at every scale preset (``calculate_row_counts``),
  ``order`` (``DependencyResolver.resolve``), ``levels`` (``RefEngine._group_by_dep_level``) and
  ``columns`` (the order of each table's columns, ``TableGenerator._order_columns``).

``--check`` regenerates both and exits 1 if they differ from what is committed, so the fixtures
cannot drift from the pinned baseline. Nothing under ``$REFENGINE_ROOT`` is modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _refpkg  # noqa: E402
from dump_schema import MODES, domain_names  # noqa: E402
from paths import REFENGINE_ROOT  # noqa: E402

FIXTURES = HERE / "fixtures"


def _plan_of(schema: Any) -> dict[str, Any]:
    RefEngine = getattr(_refpkg.mod("engine.generator"), _refpkg.ENGINE_CLASS)
    calculate_row_counts = _refpkg.mod("engine.generator").calculate_row_counts
    TableGenerator = _refpkg.mod("engine.table_generator").TableGenerator
    DependencyResolver = _refpkg.mod("schema.dependency").DependencyResolver

    per_scale: dict[str, dict[str, int]] = {}
    for scale in schema.generation.scales:
        schema.generation.scale = scale
        per_scale[scale] = {k: int(v) for k, v in calculate_row_counts(schema).items()}
    order = DependencyResolver().resolve(schema)
    levels = RefEngine._group_by_dep_level(order, schema)
    gen = TableGenerator(None, None)  # type: ignore[arg-type]
    return {
        "row_counts": per_scale,
        "order": order,
        "levels": levels,
        "columns": {t: gen._order_columns(td) for t, td in schema.tables.items()},
    }


def collect_composites() -> dict[str, dict[str, Any]]:
    """Every covered composite, keyed by harness id: its spec, merged schema dump and plan."""
    import dataclasses

    import composites

    sys.path.insert(0, str(REFENGINE_ROOT))
    out: dict[str, dict[str, Any]] = {}
    for spec in composites.specs():
        schema = composites.baseline_domain(spec).get_schema()
        preset = None
        if spec in composites.PRESETS:
            get_preset = _refpkg.mod("presets").get_preset

            p = get_preset(spec)
            preset = {
                "name": p.name,
                "description": p.description,
                "domains": list(p.domains),
                "shared_entities": p.shared_entities,
            }
        out[composites.harness_id(spec)] = {
            "spec": spec,
            "preset": preset,
            "schema": json.loads(json.dumps(dataclasses.asdict(schema), indent=1, default=str)),
            "plan": _plan_of(schema),
        }
    return out


def collect() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``(schemas, plan)``: every dump document and the plan, keyed ``<domain>_<mode>``."""
    sys.path.insert(0, str(REFENGINE_ROOT))
    import dataclasses

    _resolve_domain = _refpkg.mod("cli")._resolve_domain
    RefEngine = getattr(_refpkg.mod("engine.generator"), _refpkg.ENGINE_CLASS)
    calculate_row_counts = _refpkg.mod("engine.generator").calculate_row_counts
    TableGenerator = _refpkg.mod("engine.table_generator").TableGenerator
    DependencyResolver = _refpkg.mod("schema.dependency").DependencyResolver

    schemas: dict[str, dict[str, Any]] = {}
    plan: dict[str, Any] = {}
    for domain in domain_names():
        for mode in MODES:
            key = f"{domain}_{mode}"
            schema = _resolve_domain(domain, mode)._build_schema()
            schemas[key] = json.loads(json.dumps(dataclasses.asdict(schema), indent=1, default=str))
            per_scale: dict[str, dict[str, int]] = {}
            for scale in schema.generation.scales:
                schema.generation.scale = scale
                per_scale[scale] = {k: int(v) for k, v in calculate_row_counts(schema).items()}
            order = DependencyResolver().resolve(schema)
            levels = RefEngine._group_by_dep_level(order, schema)
            gen = TableGenerator(None, None)  # type: ignore[arg-type]
            plan[key] = {
                "row_counts": per_scale,
                "order": order,
                "levels": levels,
                "columns": {t: gen._order_columns(td) for t, td in schema.tables.items()},
            }
    return schemas, plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    args = ap.parse_args(argv)
    schemas, plan = collect()
    comps = collect_composites()
    if args.check:
        bad = []
        for key, doc in schemas.items():
            path = FIXTURES / "schemas" / f"{key}.json"
            if not path.is_file() or json.loads(path.read_text("utf-8")) != doc:
                bad.append(str(path.relative_to(HERE)))
        plan_path = FIXTURES / "plan.json"
        if not plan_path.is_file() or json.loads(plan_path.read_text("utf-8")) != plan:
            bad.append("fixtures/plan.json")
        extra = {p.stem for p in (FIXTURES / "schemas").glob("*.json")} - set(schemas)
        bad += [f"fixtures/schemas/{k}.json (not a baseline schema)" for k in sorted(extra)]
        for key, doc in comps.items():
            path = FIXTURES / "composites" / f"{key}.json"
            if not path.is_file() or json.loads(path.read_text("utf-8")) != doc:
                bad.append(str(path.relative_to(HERE)))
        extra = {p.stem for p in (FIXTURES / "composites").glob("*.json")} - set(comps)
        bad += [f"fixtures/composites/{k}.json (not a baseline composite)" for k in sorted(extra)]
        for b in bad:
            print(f"differs: {b}")
        print(
            f"{len(schemas)} schemas, {len(comps)} composites and the plan: "
            + ("DIFFER" if bad else "match the baseline")
        )
        return 1 if bad else 0
    (FIXTURES / "schemas").mkdir(parents=True, exist_ok=True)
    for key, doc in schemas.items():
        (FIXTURES / "schemas" / f"{key}.json").write_text(json.dumps(doc, indent=1) + "\n")
    (FIXTURES / "plan.json").write_text(json.dumps(plan, indent=1, sort_keys=True) + "\n")
    (FIXTURES / "composites").mkdir(parents=True, exist_ok=True)
    for key, doc in comps.items():
        (FIXTURES / "composites" / f"{key}.json").write_text(
            json.dumps(doc, indent=1, sort_keys=True) + "\n"
        )
    print(f"wrote {len(schemas)} schemas, {len(comps)} composites and plan.json to {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
