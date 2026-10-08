"""``shape report-card REAL SYNTHETIC``: one local report card for a synthetic dataset (W3-05).

Runs the fidelity scores and tiers, the utility gate and the memorization gate, and the
membership-inference test, and writes the card as JSON, Markdown or HTML (by file extension).
Exit 0 when every section that ran passed, 1 when one failed (or a required one was not run),
2 for unusable input. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_EXTENSIONS = {".json": "json", ".md": "md", ".html": "html", ".htm": "html"}


def add_arguments(sub: Any) -> None:
    rc = sub.add_parser(
        "report-card",
        help="one report card for a synthetic dataset: fidelity, utility and privacy",
        description=(
            "Compare SYNTHETIC with REAL (a file, or a directory of one file per table) and state, "
            "per section, what passed, what failed and what was not run: fidelity (scores, tiers), "
            "utility (the verify configuration's `utility` section) and privacy (the memorization "
            "gate and, with --holdout, a membership-inference test). Exit 0 when every section "
            "that ran passed, 1 when one failed, 2 for unusable input. See docs/REPORT_CARD.md."
        ),
    )
    rc.add_argument("real", metavar="REAL")
    rc.add_argument("synthetic", metavar="SYNTHETIC")
    rc.add_argument(
        "--config", metavar="VERIFY.json", help="a shape-verify-config file (see docs/VERIFY.md)"
    )
    rc.add_argument(
        "--tiers", default="1,2", metavar="1,2", help="fidelity tiers to run (default 1,2)"
    )
    rc.add_argument(
        "--holdout",
        metavar="HOLDOUT",
        help="real rows that were not given to the generator (same layout as REAL): turns on the "
        "membership-inference test",
    )
    rc.add_argument(
        "--manifest",
        metavar="RUN_MANIFEST.json",
        help="the run manifest of the generation: adds its reproducibility tuple and dataset id",
    )
    rc.add_argument(
        "-o",
        "--output",
        action="append",
        default=[],
        metavar="CARD",
        help="write the card here; .json, .md or .html by extension (repeatable)",
    )
    rc.add_argument("--json", action="store_true", help="print the card as JSON, not Markdown")
    rc.add_argument(
        "--require",
        default="",
        metavar="SECTION,...",
        help="sections (fidelity, utility, privacy) that must have run: one that was not run "
        "fails the card, and a missing scikit-learn is exit 2 for utility",
    )


def _tiers(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise ValueError(f"--tiers: {part!r} is not a tier number (use 1 and/or 2)") from None
    return out


def run(a: argparse.Namespace) -> int:
    from shape.quality.reportcard import render_html, render_markdown, report_card

    formats = []
    for out in a.output:
        fmt = _EXTENSIONS.get(Path(out).suffix.lower())
        if fmt is None:
            raise ValueError(f"cannot tell the card format of {out}: use .json, .md or .html")
        formats.append(fmt)
    card = report_card(
        a.real,
        a.synthetic,
        config=a.config,
        tiers=_tiers(a.tiers),
        holdout=a.holdout,
        manifest=a.manifest,
        require=[s.strip() for s in a.require.split(",") if s.strip()],
    )
    data = card.to_dict()
    for out, fmt in zip(a.output, formats, strict=True):
        text = (
            json.dumps(data, indent=2, allow_nan=False) + "\n"
            if fmt == "json"
            else render_markdown(data)
            if fmt == "md"
            else render_html(data)
        )
        Path(out).write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(
        json.dumps(data, indent=2, allow_nan=False) + "\n" if a.json else render_markdown(data)
    )
    return card.exit_code
