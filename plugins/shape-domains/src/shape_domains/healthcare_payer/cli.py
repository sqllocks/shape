"""``shape healthcare-payer``: generate the domain's tables, the quality report, member
timelines and the review kit."""

from __future__ import annotations

import argparse
import json
from datetime import date
from typing import Any

SHAPE_API = "1.0"


class HealthcarePayerCommand:
    """``shape healthcare-payer {generate,quality,timeline,kit,kit-score,calibration} ...``."""

    name = "healthcare-payer"
    help = "payer domain: members, medical and pharmacy claims, risk scores, timelines, review kit"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="hcp_cmd", required=True, metavar="ACTION")

        def common(p: argparse.ArgumentParser) -> None:
            p.add_argument(
                "--members", type=int, default=2000, help="members to simulate (default 2000)"
            )
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--from", dest="start", default="2022-01-01")
            p.add_argument("--to", dest="end", default="2024-12-31")
            p.add_argument(
                "--cpt-table", help="licensed CPT / revenue / type-of-bill CSV (table,key,value)"
            )

        g = sub.add_parser("generate", help="write the tables")
        common(g)
        g.add_argument("-o", "--out", required=True)
        g.add_argument("--format", choices=("parquet", "csv"), default="parquet")
        g.set_defaults(run=self._generate)
        q = sub.add_parser("quality", help="measure acceptance items 1-7 and print the table")
        common(q)
        q.add_argument("--json", action="store_true")
        q.set_defaults(run=self._quality)
        t = sub.add_parser("timeline", help="write HTML member timelines")
        common(t)
        t.add_argument("-o", "--out", required=True)
        t.add_argument("--count", type=int, default=10)
        t.add_argument("--member", action="append", help="member id (repeatable)")
        t.set_defaults(run=self._timeline)
        k = sub.add_parser("kit", help="build the blinded clinician review kit")
        common(k)
        k.add_argument("-o", "--out", required=True)
        k.add_argument("--cases", type=int, default=20)
        k.add_argument(
            "--real-tables", help="directory of de-identified real tables (Parquet, same schema)"
        )
        k.set_defaults(run=self._kit)
        s = sub.add_parser("kit-score", help="score filled review sheets against the key")
        s.add_argument("--key", required=True)
        s.add_argument("sheets", nargs="+")
        s.set_defaults(run=self._score)
        c = sub.add_parser(
            "calibration", help="print the calibration table (every rate and its source)"
        )
        c.set_defaults(run=self._calibration)

    def run(self, args: Any) -> int:
        return int(args.run(args))

    # ---- actions -------------------------------------------------------------------------------
    @staticmethod
    def _data(args: Any) -> Any:
        from .byo import load_licensed
        from .generate import generate

        lic = load_licensed(args.cpt_table) if args.cpt_table else None
        return generate(
            args.members,
            seed=args.seed,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            licensed=lic,
        )

    def _generate(self, args: Any) -> int:
        data = self._data(args)
        paths = data.write(args.out, args.format)
        print(
            f"wrote {len(paths)} tables to {args.out}: "
            + ", ".join(f"{n} ({t.num_rows})" for n, t in data.tables.items())
        )
        return 0

    def _quality(self, args: Any) -> int:
        from . import quality

        checks = quality.run_all(self._data(args))
        if args.json:
            print(
                json.dumps(
                    [
                        {"item": c.item, "title": c.title, "passed": c.passed, "metrics": c.metrics}
                        for c in checks
                    ],
                    default=str,
                    indent=2,
                )
            )
        else:
            print(quality.render_markdown(checks))
        return 0 if all(c.passed for c in checks) else 1

    def _timeline(self, args: Any) -> int:
        from . import report

        data = self._data(args)
        ids = args.member or report.pick_rich_members(data.tables, args.count)
        paths = report.write_timelines(data.tables, args.out, ids)
        print(f"wrote {len(paths)} timelines to {args.out}")
        return 0

    def _kit(self, args: Any) -> int:
        from . import kit

        data = self._data(args)
        res = kit.build_kit(
            data.tables, args.out, n_synthetic=args.cases, real_tables=args.real_tables
        )
        print(
            f"kit in {res.directory}: {res.cases} cases "
            f"({res.synthetic} generated, {res.real} real)"
        )
        if res.real == 0:
            print(
                "no real cases: the blinded review is [VERIFY] until a de-identified real "
                "sample is added"
            )
        return 0

    @staticmethod
    def _score(args: Any) -> int:
        from . import kit

        result = kit.score(args.key, args.sheets)
        print(json.dumps(result, indent=2))
        return 0 if result["pass"] else 1

    @staticmethod
    def _calibration(args: Any) -> int:
        from .calibration import render_markdown

        print(render_markdown())
        return 0
