"""Load and validate one reference input with Shape's pack code (Shape venv).

    $SHAPE_PY shape_worker.py --input FILE --kind pack|spec

Prints one JSON document with the parsed structure and the validation verdict, in the same
shape as ``baseline_worker.py``. The run itself goes through the ``shape pack run`` command
(``verify.py`` calls it), not through this module.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from shape.scenario import (
    GSLParser,
    PackLoader,
    PackValidator,
    spec_domain,
    spec_pack,
    validate_spec,
)
from shape.scenario.resolve import load_pack_domain


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--kind", choices=("pack", "spec"), required=True)
    a = ap.parse_args()
    path = Path(a.input)
    result: dict = {"kind": a.kind}
    if a.kind == "spec":
        spec = GSLParser().parse(path)
        loaded = dataclasses.asdict(spec)
        loaded.pop("_base_dir", None)
        pack = spec_pack(spec)
        result["pack_structure"] = dataclasses.asdict(pack)
        vr_spec = validate_spec(spec)
        result["spec_validation"] = {
            "is_valid": vr_spec.is_valid,
            "errors": vr_spec.errors,
            "warnings": vr_spec.warnings,
        }
        domain = spec_domain(spec)
    else:
        pack = PackLoader().load(path)
        loaded = dataclasses.asdict(pack)
        domain = load_pack_domain(pack.domain)
    vr = PackValidator().validate(pack, domain)
    result["loaded"] = loaded
    result["validation"] = {"is_valid": vr.is_valid, "errors": vr.errors, "warnings": vr.warnings}
    print("RESULT_JSON " + json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
