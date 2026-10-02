"""Run one reference input through the pinned baseline's pack code (baseline venv).

    $SPINDLE_PY baseline_worker.py --input FILE --kind pack|spec --seeds 42,43 --out DIR

Writes ``DIR/seed<N>/`` for each seed (a pack run's output, with its manifest) and prints one
JSON document: the parsed structure, the validation verdict and, per seed, the run's result.
The baseline has no command that runs a spec, so for a spec this driver does what its parser
and runner allow: parse the spec, load the pack it points at, resolve its domain, apply the three
overrides that Shape's spec run defines (the spec's gates replace the pack's, its landing-zone
root replaces ``lakehouse_files_root``, its lakehouse tables replace the file-drop entities) and
run the baseline's own ``PackRunner`` at the scale and seed given here. ``--probe`` catches an
exception from loading or running and reports it instead (the defect probes use it).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import SPINDLE_ROOT  # noqa: E402

sys.path.insert(0, str(SPINDLE_ROOT))
warnings.simplefilter("ignore")

from sqllocks_spindle.cli import _resolve_domain  # noqa: E402
from sqllocks_spindle.packs.loader import PackLoader  # noqa: E402
from sqllocks_spindle.packs.runner import PackRunner  # noqa: E402
from sqllocks_spindle.packs.validator import PackValidator  # noqa: E402
from sqllocks_spindle.specs.gsl_parser import GSLParser  # noqa: E402


def apply_spec(pack, spec) -> None:
    """The overrides a spec sets, applied to the baseline's pack object."""
    from sqllocks_spindle.packs.loader import ValidationSpec

    if spec.validation is not None and spec.validation.gates:
        if pack.validation is None:
            pack.validation = ValidationSpec()
        pack.validation.required_gates = list(spec.validation.gates)
    lake = spec.outputs.lakehouse if spec.outputs else None
    if lake is not None:
        if lake.landing_zone is not None and lake.landing_zone.root:
            pack.fabric_targets = {
                **pack.fabric_targets,
                "lakehouse_files_root": lake.landing_zone.root,
            }
        if lake.tables and pack.file_drop is not None:
            pack.file_drop.entities = list(lake.tables)


def probe(a) -> int:
    """One run that may fail: report the exception instead of raising."""
    out = Path(a.out)
    result: dict = {"exception": None}
    try:
        pack = PackLoader().load(a.input)
        domain = _resolve_domain(pack.domain, "3nf")
        vr = PackValidator().validate(pack, domain)
        result["validation"] = {
            "is_valid": vr.is_valid,
            "errors": vr.errors,
            "warnings": vr.warnings,
        }
        run = PackRunner().run(pack, domain, scale=a.scale, seed=42, base_path=str(out))
        result["run"] = {
            "success": run.is_success,
            "errors": run.errors,
            "gates": run.validation_results,
            "files": sorted(str(f) for f in run.files_written),
            "manifest_tables": {
                t: v["file_paths"] for t, v in (run.manifest.tables.items() if run.manifest else [])
            },
        }
    except Exception as exc:  # noqa: BLE001 - the probe reports whatever the baseline does
        result["exception"] = f"{type(exc).__name__}: {exc}"
    print("RESULT_JSON " + json.dumps(result, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--kind", choices=("pack", "spec"), required=True)
    ap.add_argument("--scale", default="fabric_demo")
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        return probe(a)
    path, out = Path(a.input), Path(a.out)
    result: dict = {"kind": a.kind}
    if a.kind == "spec":
        spec = GSLParser().parse(path)
        loaded = dataclasses.asdict(spec)
        loaded.pop("_base_dir", None)
        pack = PackLoader().load(spec.resolve_path(spec.scenario.pack))
        result["pack_structure"] = dataclasses.asdict(pack)
        apply_spec(pack, spec)
        domain_name = spec.schema.domain
    else:
        pack = PackLoader().load(path)
        loaded = dataclasses.asdict(pack)
        domain_name = pack.domain
    domain = _resolve_domain(domain_name, "3nf")
    vr = PackValidator().validate(pack, domain)
    result["loaded"] = loaded
    result["validation"] = {
        "is_valid": vr.is_valid,
        "errors": vr.errors,
        "warnings": vr.warnings,
    }
    result["runs"] = {}
    for seed in (int(s) for s in a.seeds.split(",")):
        dest = out / f"seed{seed}"
        run = PackRunner().run(pack, domain, scale=a.scale, seed=seed, base_path=str(dest))
        manifest = None
        mf = sorted(dest.glob("*_manifest.json"))
        if mf:
            manifest = json.loads(mf[-1].read_text())
        result["runs"][str(seed)] = {
            "success": run.is_success,
            "errors": run.errors,
            "events": run.events_emitted,
            "gates": run.validation_results,
            "files": sorted(
                str(Path(f).resolve().relative_to(dest.resolve())) for f in run.files_written
            ),
            "manifest": manifest,
        }
    print("RESULT_JSON " + json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
