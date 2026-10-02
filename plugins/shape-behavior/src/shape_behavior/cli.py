"""``shape behave``: run, check and import behavior modules (``docs/plugins/behavior.md``, section 7).

::

    shape behave run subscription --population 10000 --years 3 --seed 7 -o out/
    shape behave check my_module.json
    shape behave import-gmf downloaded_module.json -o converted.json
    shape behave examples -o modules/

Nothing heavy loads at import time: NumPy, Arrow and the simulator load when a command runs.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

SHAPE_API = "1.0"

DEFAULT_POPULATION: dict[str, Any] = {
    "age_at_start": {"kind": "uniform", "low": 0, "high": 90},
    "attributes": {"gender": {"kind": "categorical", "values": {"F": 0.5, "M": 0.5}}},
}


class BehaveCommand:
    """``shape behave COMMAND ...``."""

    name = "behave"
    help = "run state-machine behavior modules on a virtual clock (events as Parquet)"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="behave_cmd", required=True, metavar="COMMAND")
        run = sub.add_parser("run", help="simulate a population and write the event stream")
        run.add_argument("modules", nargs="+", metavar="MODULES", help="module files (native or GMF JSON) or example names")
        run.add_argument("--population", type=int, required=True, metavar="N", help="number of entities")
        run.add_argument("--years", type=float, required=True, metavar="Y", help="years to simulate")
        run.add_argument("--seed", type=int, default=0, help="random seed (default 0)")
        run.add_argument("-o", "--out", required=True, metavar="OUT", help="output directory")
        run.add_argument("--start", default="2020-01-01", help="virtual clock start (default 2020-01-01)")
        run.add_argument("--population-spec", metavar="FILE", help="JSON population settings (attributes, age_at_start, arrival, lifetime)")
        run.add_argument("--window-years", type=float, default=1.0, help="years per output file (default 1)")
        run.add_argument("--poll", default="7 days", help="guard polling interval (default '7 days')")
        run.add_argument("--strict", action="store_true", help="fail on unsupported GMF elements")
        run.set_defaults(fn=_run)
        check = sub.add_parser("check", help="validate modules; exit 1 when one is invalid")
        check.add_argument("modules", nargs="+", metavar="MODULES")
        check.add_argument("--strict", action="store_true", help="fail on unsupported GMF elements")
        check.set_defaults(fn=_check)
        imp = sub.add_parser("import-gmf", help="convert a GMF module and report what is unsupported")
        imp.add_argument("file", help="the GMF JSON file you downloaded")
        imp.add_argument("-o", "--out", metavar="OUT.json", help="write the native module here")
        imp.add_argument("--strict", action="store_true", help="fail on unsupported elements")
        imp.set_defaults(fn=_import)
        ex = sub.add_parser("examples", help="list the built-in example modules, or write them")
        ex.add_argument("-o", "--out", metavar="DIR", help="write the examples into DIR")
        ex.set_defaults(fn=_examples)

    def run(self, args: Any) -> int:
        try:
            return int(args.fn(args))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"shape behave: {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:  # includes ModuleError, UnsupportedGmfError
            print(f"shape behave: {exc}", file=sys.stderr)
            return 1


def _load(sources: list[str], strict: bool) -> list[Any]:
    from shape_behavior.model import load_module

    modules = []
    for src in sources:
        module = load_module(src, strict=strict)
        report = module.import_report
        if report is not None and (report.unsupported or report.warnings):
            print(f"{src}:\n{report.report()}", file=sys.stderr)
        modules.append(module)
    return modules


def _run(args: Any) -> int:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    from shape_behavior.population import Population
    from shape_behavior.simulator import SimConfig, Simulator
    from shape_behavior.timeutil import add_years, to_us

    if args.population < 0 or args.years <= 0 or args.window_years <= 0:
        print("shape behave: --population must be >= 0 and --years and --window-years > 0", file=sys.stderr)
        return 2
    modules = _load(args.modules, args.strict)
    if args.population_spec:
        spec = json.loads(Path(args.population_spec).read_text(encoding="utf-8"))
    else:
        spec = {}
        for m in reversed(modules):
            spec.update(m.doc.get("population_defaults", {}))
        if not spec:
            spec = json.loads(json.dumps(DEFAULT_POPULATION))
    population = Population.from_dict(spec, size=args.population, start=args.start)
    sim = Simulator(modules, population, SimConfig(seed=args.seed, poll=args.poll))
    out = Path(args.out)
    (out / "events").mkdir(parents=True, exist_ok=True)
    start = to_us(args.start)
    end = add_years(start, args.years)
    started = time.perf_counter()
    windows: list[dict[str, Any]] = []
    cursor, part = start, 0
    while cursor < end:
        nxt = min(add_years(start, args.window_years * (part + 1)), end)
        events = sim.run_until(nxt)
        pq.write_table(events, out / "events" / f"part-{part:04d}.parquet", compression="snappy")
        windows.append({"file": f"events/part-{part:04d}.parquet", "until": str(sim.now), "rows": events.num_rows})
        cursor, part = nxt, part + 1
    pq.write_table(sim.entities(), out / "entities.parquet", compression="snappy")
    elapsed = time.perf_counter() - started
    total = sum(w["rows"] for w in windows)
    manifest = {
        "seed": args.seed,
        "years": args.years,
        "start": args.start,
        "population": population.to_dict(),
        "modules": [{"name": m.name, "digest": m.digest(), "source": s} for m, s in zip(modules, args.modules, strict=True)],
        "events": total,
        "windows": windows,
        "seconds": round(elapsed, 3),
        "unsupported": [str(u) for m in modules if m.import_report for u in m.import_report.unsupported],
    }
    (out / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{total:,} events for {args.population:,} entities in {elapsed:.1f}s -> {out}")
    return 0


def _check(args: Any) -> int:
    modules = _load(args.modules, args.strict)
    for m in modules:
        print(f"ok  {m.name}  ({len(m.states)} states)")
    return 0


def _import(args: Any) -> int:
    from shape_behavior.gmf import import_gmf

    result = import_gmf(Path(args.file), strict=args.strict)
    print(result.report())
    if args.out:
        Path(args.out).write_text(json.dumps(result.module.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def _examples(args: Any) -> int:
    import importlib.resources

    from shape_behavior.model import examples

    names = examples()
    if not args.out:
        print("\n".join(names))
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in names:
        text = (importlib.resources.files("shape_behavior") / "examples" / f"{name}.json").read_text(encoding="utf-8")
        (out / f"{name}.json").write_text(text, encoding="utf-8")
    print(f"wrote {len(names)} modules to {out}")
    return 0
