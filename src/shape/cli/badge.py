"""``shape badge``: a status badge from ``shape-result`` documents (W6-01).

The badge is a self-contained SVG: no external reference, no font, no script. Its width comes
from a fixed table of character widths (:data:`WIDTHS`, in pixels at 11 px Verdana), not from a
font, so the same inputs give the same bytes on every machine.

States, from the results given (the worst one wins):

``failing``  a result whose exit code is not 0 or 2 (a check failed);
``unknown``  no result, or a result whose exit code is 2 (the command could not run);
``drift``    every command ran and drift was reported, with no enforced gate failing;
``passing``  every command ran and reported nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shape.cli.findings import Result, ResultError, load_all

DEFAULT_LABEL = "shape"
MAX_LABEL = 40
STATES = ("passing", "drift", "failing", "unknown")
COLORS = {
    "passing": "#3fb950",
    "drift": "#d29922",
    "failing": "#e5534b",
    "unknown": "#8b949e",
}
LABEL_COLOR = "#555555"
HEIGHT = 20
PAD = 6
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: width in pixels of each printable ASCII character at 11 px Verdana (rounded to whole pixels)
WIDTHS: dict[str, int] = {}
for _width, _chars in (
    (3, " ',.:;|!iIjl`"),
    (4, "()-/[]\\ftr{}"),
    (5, '"*^'),
    (6, "Jcksvxyz_"),
    (7, "0123456789#$?abdeghnopqu+<=>~FLTZ"),
    (8, "&ABCEKPSVXY"),
    (9, "DGHNRUw"),
    (10, "COQm%M"),
    (11, "@"),
    (12, "W"),
):
    for _c in _chars:
        WIDTHS[_c] = _width
del _width, _chars, _c
#: any other character (not ASCII) is given this width
DEFAULT_WIDTH = 8


def text_width(text: str) -> int:
    """The width of ``text`` in pixels, from the character-width table."""
    return sum(WIDTHS.get(ch, DEFAULT_WIDTH) for ch in text)


def state_of(results: Sequence[Result]) -> str:
    """The state of the badge for ``results`` (none gives ``unknown``)."""
    if not results:
        return "unknown"
    if any(r.exit_code not in (0, 2) for r in results):
        return "failing"
    if any(r.exit_code == 2 for r in results):
        return "unknown"
    return "drift" if any(r.findings for r in results) else "passing"


def _xml(text: str) -> str:
    clean = _CONTROL.sub("", text)
    return (
        clean.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def svg(label: str, state: str) -> str:
    """The badge as SVG text (``state`` is one of :data:`STATES`)."""
    left = text_width(label) + 2 * PAD
    right = text_width(state) + 2 * PAD
    total = left + right
    title = _xml(f"{label}: {state}")
    lx = left * 5  # text is placed in tenths of a pixel, centred
    rx = left * 10 + right * 5
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{HEIGHT}" '
        f'role="img" aria-label="{title}">\n'
        f"<title>{title}</title>\n"
        f'<rect width="{left}" height="{HEIGHT}" fill="{LABEL_COLOR}"/>\n'
        f'<rect x="{left}" width="{right}" height="{HEIGHT}" fill="{COLORS[state]}"/>\n'
        '<g fill="#ffffff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" '
        'font-size="110" transform="scale(.1)">\n'
        f'<text x="{lx}" y="140">{_xml(label)}</text>\n'
        f'<text x="{rx}" y="140">{state}</text>\n'
        "</g>\n</svg>\n"
    )


def add_arguments(sub: Any) -> None:
    b = sub.add_parser(
        "badge",
        help="write a status badge (SVG) from shape-result documents",
        description="Write a self-contained SVG badge: passing (green), drift (amber), failing "
        "(red) or unknown (grey: no result, or a result whose exit code is 2). Without any "
        "RESULT.json the badge is unknown.",
    )
    b.add_argument("results", nargs="*", metavar="RESULT.json")
    b.add_argument("-o", "--output", required=True, metavar="badge.svg")
    b.add_argument("--label", default=DEFAULT_LABEL, metavar="TEXT", help="left-hand text")
    b.add_argument("--json", action="store_true", help="print the state as a shape-result")


def run(a: argparse.Namespace) -> int:
    label = _CONTROL.sub("", a.label).strip()
    if not label or len(label) > MAX_LABEL or "\n" in a.label:
        print(f"shape: error: --label must be 1 to {MAX_LABEL} characters", file=sys.stderr)
        return 2
    try:
        results = load_all(a.results)
    except ResultError as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2
    state = state_of(results)
    from shape.cli import ci

    ci.write_text(Path(a.output), svg(label, state), "badge", newline="\n")
    print(json.dumps({"state": state, "label": label, "badge": str(a.output)}))
    return 0
