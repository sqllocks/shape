"""The ``shape`` subcommands of the plugin (``shape.commands``): ``from-dbt``, ``to-dbt-tests``,
``dbt-seeds`` and ``dbt-report``. Exit codes follow Shape's: 0 success, 1 a failed check, 2 a
usage or input error."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _fail(message: str) -> int:
    print(f"shape: error: {message}", file=sys.stderr)
    return 2


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, doc: Any) -> None:
    _write(path, json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n")


def _errors() -> tuple[type[Exception], ...]:
    from shape.contracts.v1 import ContractError
    from shape.errors import ShapeError

    from .project import DbtProjectError
    from .report import ReportError
    from .seeds import SeedError

    return (DbtProjectError, ReportError, SeedError, ContractError, ShapeError, OSError)


class _Guarded:
    """Runs the command and turns an input error into exit code 2 with a one-line message."""

    name = ""
    help = ""

    def configure(self, parser: Any) -> None:
        raise NotImplementedError

    def execute(self, args: Any) -> int:
        raise NotImplementedError

    def run(self, args: Any) -> int:
        try:
            return self.execute(args)
        except _errors() as exc:
            return _fail(str(exc))


class FromDbt(_Guarded):
    name = "from-dbt"
    help = "read a dbt project (manifest.json, schema.yml, sources.yml) into a generation schema"

    def configure(self, parser: Any) -> None:
        parser.description = (
            "Reads sources, seeds and models with their tests: unique and not_null give keys, "
            "relationships give foreign keys, accepted_values give weighted enums, and a model "
            "contract's data_type gives the column type (decimal precision and scale included)."
        )
        parser.add_argument(
            "inputs",
            nargs="+",
            metavar="PATH",
            help="manifest.json, schema.yml or sources.yml files, or a dbt project directory",
        )
        parser.add_argument("-o", "--output", metavar="OUT", help="default: ./dbt_import.gen.json")
        parser.add_argument("--domain", default="custom", help="domain name for the schema")
        parser.add_argument(
            "--select",
            action="append",
            choices=("source", "seed", "model"),
            help="what to generate (repeatable); default: sources and seeds, else models",
        )
        parser.add_argument("-s", "--scale", metavar="SPEC", help="small:table1=N,table2=N")
        parser.add_argument(
            "--profile",
            metavar="PROFILE.shape",
            help="a profile whose value frequencies weight the accepted_values columns",
        )
        parser.add_argument("--no-smart", action="store_true", help="keep the first generators")
        parser.add_argument("--explain", action="store_true", help="print what was inferred")

    def execute(self, args: Any) -> int:
        import shape

        from .fromdbt import from_dbt, metadata
        from .project import read_project

        relations = read_project(args.inputs)
        profile = shape.load(args.profile) if args.profile else None
        schema, notes = from_dbt(
            relations,
            kinds=args.select,
            domain=args.domain,
            smart=not args.no_smart,
            scale=args.scale,
            profile=profile,
        )
        out = Path(args.output) if args.output else Path("dbt_import.gen.json")
        _json(out, schema.to_dict())
        meta_path = out.with_name(out.name.removesuffix(".json") + ".dbt-meta.json")
        _json(meta_path, metadata(relations, schema))
        print("Shape dbt import")
        print()
        print(f"  Output: {out}")
        print(f"  Metadata: {meta_path}")
        print(f"  Tables: {len(schema.tables)}")
        print(f"  Relationships: {len(schema.relationships)}")
        for name, table in schema.tables.items():
            pk = f" (PK: {', '.join(table.primary_key)})" if table.primary_key else ""
            print(f"  {name}: {len(table.columns)} columns{pk}")
        text_notes = [n for n in notes if isinstance(n, str)]
        for n in text_notes:
            print(f"  note: {n}")
        if args.explain:
            for n in notes:
                if not isinstance(n, str):
                    col = f".{n.column}" if n.column else ""
                    print(f"  [{n.rule_id}] {n.table}{col}: {n.description}")
        return 0


class ToDbtTests(_Guarded):
    name = "to-dbt-tests"
    help = "compile a contract or a profile into dbt schema.yml tests"

    def configure(self, parser: Any) -> None:
        parser.description = (
            "Writes schema.yml data tests that run in a dbt job with no Python: not_null, "
            "unique, accepted_values, dbt_utils.accepted_range and dbt_expectations tests. "
            "Rules dbt cannot state (dtype, pattern, distribution) are kept as column meta."
        )
        parser.add_argument(
            "source",
            metavar="CONTRACT.json|PROFILE.shape",
            help="a v1 contract, or a profile (a contract is captured from it)",
        )
        parser.add_argument("-o", "--output", metavar="schema.yml", required=True)
        parser.add_argument("--model", help="the dbt model, for a contract of one table")
        parser.add_argument(
            "--kind", choices=("models", "seeds", "sources"), default="models", help="default: models"
        )
        parser.add_argument("--source-name", default="raw", help="the dbt source, for --kind sources")
        parser.add_argument(
            "--tests-key",
            choices=("data_tests", "tests"),
            default="data_tests",
            help="`tests` for dbt before 1.8",
        )
        parser.add_argument(
            "--args-style",
            choices=("arguments", "inline"),
            default="arguments",
            help="test arguments under `arguments:` (dbt 1.10+) or inline (every version)",
        )
        parser.add_argument(
            "--distribution",
            action="store_true",
            help="from a profile: add mean, standard-deviation and quartile bound tests",
        )
        parser.add_argument(
            "--margin", type=float, default=0.05, help="from a profile: widen min/max by this share"
        )
        parser.add_argument(
            "--packages-out", metavar="packages.yml", help="also write the packages the tests need"
        )

    def execute(self, args: Any) -> int:
        from .totests import bounds_from_profile, compile_tests, contract_from_profile

        src = Path(args.source)
        bounds = None
        if src.suffix == ".shape":
            import shape

            profile = shape.load(src)
            contract = contract_from_profile(profile, margin=args.margin)
            if args.distribution:
                bounds = bounds_from_profile(profile)
            model = args.model or getattr(profile, "name", None)
        else:
            try:
                contract = json.loads(src.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return _fail(f"{src} is not valid JSON: {exc}")
            if args.distribution:
                return _fail("--distribution needs a profile (.shape), not a contract")
            model = args.model
        compiled = compile_tests(
            contract,
            model=model,
            kind=args.kind,
            source_name=args.source_name,
            bounds=bounds,
            tests_key=args.tests_key,
            args_style=args.args_style,
        )
        out = Path(args.output)
        _write(out, compiled.yaml())
        print(f"Wrote {out}")
        if args.packages_out and compiled.packages:
            _write(Path(args.packages_out), compiled.packages_yml())
            print(f"Wrote {args.packages_out}")
        elif compiled.packages:
            print("These tests need dbt packages; add to packages.yml and run `dbt deps`:")
            print(compiled.packages_yml())
        for note in compiled.notes:
            print(f"  note: {note}")
        return 0


class DbtSeeds(_Guarded):
    name = "dbt-seeds"
    help = "generate a schema's tables as CSV seeds in a dbt project"

    def configure(self, parser: Any) -> None:
        from .seeds import DIALECTS

        parser.description = (
            "Generates the tables and writes seeds/<table>.csv with a seeds: block that states "
            "every column type (identifiers with leading zeros stay text). Seeds are for small, "
            "static data: a file over 1 MiB is refused unless --allow-large."
        )
        parser.add_argument("schema", metavar="SCHEMA.gen.json", help="a generation schema")
        parser.add_argument("--project", required=True, metavar="DIR", help="the dbt project")
        parser.add_argument("--seeds-dir", default="seeds", help="default: seeds")
        parser.add_argument("--scale", help="a scale preset of the schema")
        parser.add_argument("--seed", type=int, help="the generation seed")
        parser.add_argument("--rows", metavar="T=N,...", help="row counts per table")
        parser.add_argument("--dialect", choices=sorted(DIALECTS), default="ansi")
        parser.add_argument(
            "--metadata", metavar="FILE", help="the .dbt-meta.json `from-dbt` wrote (descriptions)"
        )
        parser.add_argument("--allow-large", action="store_true", help="allow a file over 1 MiB")

    def execute(self, args: Any) -> int:
        from shape.generation.engine import Engine
        from shape.generation.schema import GenSchema

        from .seeds import DbtSeedsSink

        try:
            doc = json.loads(Path(args.schema).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _fail(f"{args.schema} is not valid JSON: {exc}")
        schema = GenSchema.from_dict(doc)
        rows: dict[str, int] = {}
        for pair in (args.rows or "").split(","):
            if pair.strip():
                name, _, count = pair.partition("=")
                try:
                    rows[name.strip()] = int(count)
                except ValueError:
                    return _fail(f"bad row count {pair!r}; use table=N")
        meta: dict[str, Any] = {}
        if args.metadata:
            meta = json.loads(Path(args.metadata).read_text(encoding="utf-8")).get("tables", {})
        result = Engine(schema, scale=args.scale, seed=args.seed, row_counts=rows).generate()
        sink = DbtSeedsSink()
        for name, table in result.tables.items():
            columns = {
                c.name: {
                    "type": c.type,
                    "precision": c.precision,
                    "scale": c.scale,
                    "max_length": c.max_length,
                }
                for c in schema.tables[name].columns.values()
            }
            described = meta.get(name, {})
            count = sink.write(
                args.project,
                name,
                table.to_batches() or [],
                seeds_dir=args.seeds_dir,
                columns=columns,
                dialect=args.dialect,
                allow_large=args.allow_large,
                description=described.get("description") or schema.tables[name].description,
                column_descriptions={
                    c: i.get("description", "") for c, i in described.get("columns", {}).items()
                },
                schema=table.schema,
            )
            print(f"  {name}: {count:,} rows -> {args.seeds_dir}/{name}.csv")
        print(f"Seeds written to {args.project}/{args.seeds_dir}; run `dbt seed` or `dbt build`")
        return 0


class DbtReport(_Guarded):
    name = "dbt-report"
    help = "one report for a dbt run (run_results.json, manifest.json) and a Shape check and drift"

    def configure(self, parser: Any) -> None:
        parser.description = (
            "Reads the output of a dbt run and, optionally, a Shape contract check and a drift "
            "comparison, and reports them together. Exit 1 when a dbt test or model failed, a "
            "contract rule was violated, or (with --fail-on-drift) the data drifted."
        )
        parser.add_argument("--run-results", required=True, metavar="run_results.json")
        parser.add_argument("--manifest", metavar="manifest.json", help="names each test's model")
        parser.add_argument("--check-result", metavar="JSON", help="a `shape check --json` result")
        parser.add_argument("--diff-result", metavar="JSON", help="a `shape diff --json` result")
        parser.add_argument("--profile", metavar="PROFILE.shape", help="check this profile ...")
        parser.add_argument("--contract", metavar="CONTRACT.json", help="... against this contract")
        parser.add_argument("--baseline", metavar="BASE.shape", help="diff --profile against this")
        parser.add_argument("--table", help="the dbt model a single-table check or diff is about")
        parser.add_argument("--fail-on-drift", action="store_true")
        parser.add_argument("-o", "--output", metavar="REPORT.json")
        parser.add_argument("--md", metavar="REPORT.md", help="also write a Markdown report")

    def execute(self, args: Any) -> int:
        from .report import ReportError, build_report, load_json, render_markdown

        run_results = load_json(args.run_results, "run_results")
        manifest = load_json(args.manifest, "manifest") if args.manifest else None
        check: dict[str, Any] | None = None
        drift: dict[str, Any] | None = None
        if args.check_result:
            check = load_json(args.check_result, "check result")
        if args.diff_result:
            drift = load_json(args.diff_result, "diff result")
        if args.profile and (args.contract or args.baseline):
            import shape

            current = shape.load(args.profile)
            if args.contract and check is None:
                check = shape.check(current, args.contract).to_dict()
            if args.baseline and drift is None:
                drift = shape.diff(shape.load(args.baseline), current).to_dict()
        elif args.contract or args.baseline:
            raise ReportError("--contract and --baseline need --profile")
        report = build_report(
            run_results,
            manifest,
            check=check,
            drift=drift,
            table=args.table,
            fail_on_drift=args.fail_on_drift,
        )
        text = render_markdown(report)
        if args.output:
            _json(Path(args.output), report)
        if args.md:
            _write(Path(args.md), text)
        print(text, end="")
        return 0 if report["ok"] else 1
