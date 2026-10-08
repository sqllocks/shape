"""``shape bisect`` (and ``shape bisect layers``) and ``shape timelapse`` (W3-03).

``bisect`` finds the first committed version of a name that tests bad; ``bisect layers`` finds
the layer of a pipeline where a change appears; ``timelapse`` follows one column across the
versions. Exit codes: ``bisect`` 0 found, 2 unusable input or a range that cannot be searched;
``bisect layers`` 0 a layer shows the change, 1 none does, 2 unusable input; ``timelapse`` 0, 2.
Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def _dump(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True, default=str))


def add_arguments(sub: Any, add_policy: Callable[[argparse.ArgumentParser], None]) -> None:
    from shape.cli.project import add_project_flags

    b = sub.add_parser(
        "bisect",
        help="find the first committed version of a name that changed (git bisect for data)",
        description="Binary search over the versions of NAME in a registry, between --good and "
        "--bad (registry refs), for the first version that tests bad. The default test is "
        "`shape diff` against the good version, under the thresholds and ignore lists of the "
        "shape.yml source (flags override); --contract FILE tests 'the contract fails' instead. "
        "Exit 0 when found, 2 when --good tests bad, --bad tests good, or a version cannot be "
        "tested. `shape bisect layers ...` finds the layer of a pipeline where a change appears "
        "(see `shape bisect layers --help`). See docs/HISTORY.md.",
    )
    b.add_argument("registry", metavar="REGISTRY", help="the registry directory")
    b.add_argument("name", metavar="NAME", help="the committed name whose versions are searched")
    b.add_argument("--good", required=True, metavar="REF", help="a version known to be good")
    b.add_argument("--bad", required=True, metavar="REF", help="a later version known to be bad")
    b.add_argument("--column", metavar="COL", help="only changes of this column count")
    b.add_argument("--kind", metavar="KIND", help="only changes of this diff kind count")
    b.add_argument("--contract", metavar="FILE", help="a version is bad when this contract fails")
    b.add_argument(
        "--verify-all",
        action="store_true",
        help="test every version instead of bisecting, and report any that flips back to good",
    )
    b.add_argument(
        "--coarse",
        choices=("week", "month"),
        help="bisect over merged windows first (profiles need `shape profile --sketches`)",
    )
    b.add_argument("--json", action="store_true", help="print the result as JSON")
    add_project_flags(b)
    add_policy(b)

    t = sub.add_parser(
        "timelapse",
        help="one column's statistics across the committed versions of a name",
        description="One frame per version (or merged window): rows, null rate, distinct "
        "estimate, quantiles, mean, standard deviation and top values, read from the stored "
        "profiles. Frames where `shape diff` reports a change are change points. -o OUT.html is "
        "one self-contained page (works offline); -o OUT.json the data. See docs/HISTORY.md.",
    )
    t.add_argument("registry", metavar="REGISTRY", help="the registry directory")
    t.add_argument("name", metavar="NAME")
    t.add_argument("--column", required=True, metavar="COL")
    t.add_argument("--table", metavar="T", help="the table of a dataset profile")
    t.add_argument("--since", metavar="DATE", help="first date (inclusive, YYYY-MM-DD)")
    t.add_argument("--until", metavar="DATE", help="last date (inclusive, YYYY-MM-DD)")
    t.add_argument(
        "--window",
        choices=("day", "week", "month"),
        help="merge the versions of each period (profiles need `shape profile --sketches`)",
    )
    t.add_argument("-o", "--output", metavar="OUT.json|OUT.html", help="write a file")
    t.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="what goes to standard output (default json); `text` is a sparkline per statistic",
    )
    add_project_flags(t)
    add_policy(t)


def _layers_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shape bisect layers",
        description="Find the layer of a pipeline where a change first appears. Each layer is a "
        "source of shape.yml (its baseline gives the registry and the name, its thresholds apply). "
        "For each layer the version at --bad-date is diffed against the version at --good-date "
        "(the newest on or before each). Exit 0 when a layer shows the change, 1 when none does, "
        "2 for unusable input. See docs/HISTORY.md.",
    )
    p.add_argument(
        "--layers", required=True, metavar="SOURCE[,SOURCE...]", help="in pipeline order"
    )
    p.add_argument("--good-date", required=True, metavar="D1")
    p.add_argument("--bad-date", required=True, metavar="D2")
    p.add_argument("--column", metavar="COL", help="the column, by the name the --map values use")
    p.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="LAYER.COL=COL",
        help="the column COL of the change is called COL in LAYER (repeatable)",
    )
    p.add_argument("--project", metavar="shape.yml", help="default: the nearest shape.yml")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    return p


def run_layers(argv: Sequence[str]) -> int:
    """``shape bisect layers ...`` (``argv`` starts after ``layers``)."""
    from shape.cli import errors
    from shape.versions import bisect_layers

    a = _layers_parser().parse_args(list(argv))
    try:
        mapping = {}
        for item in a.map:
            left, sep, right = item.partition("=")
            if not sep:
                raise ValueError(f"--map wants LAYER.COL=COL, got {item!r}")
            mapping[left] = right
        result = bisect_layers(
            [x.strip() for x in a.layers.split(",") if x.strip()],
            good_date=a.good_date,
            bad_date=a.bad_date,
            column=a.column,
            mapping=mapping,
            project=a.project,
        )
    except errors.EXPECTED as exc:
        if errors.debug_enabled():
            raise
        return int(errors.fail(exc))
    if a.json:
        _dump(result.to_dict())
    else:
        print(_layers_text(result.to_dict()))
    return 0 if result.found else 1


def _change_line(c: dict[str, Any]) -> str:
    return f"{c['column']}: {c['kind']} {_short(c['before'])} -> {_short(c['after'])}"


def _short(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    text = json.dumps(v, default=str) if not isinstance(v, str) else v
    return text if len(text) <= 40 else text[:37] + "..."


def _layers_text(d: dict[str, Any]) -> str:
    lines = [f"layers, {d['good_date']} -> {d['bad_date']}:"]
    for layer in d["layers"]:
        what = "; ".join(_change_line(c) for c in layer["changes"][:3]) or "-"
        lines.append(f"  {layer['source']:<16}{layer['status']:<11}{what}")
    if d["found"]:
        lines.append(f"the change first appears in layer {d['first_layer']}")
        lines.append(f"  persists in: {', '.join(d['persists']) or '-'}")
        lines.append(f"  disappears in: {', '.join(d['disappears']) or '-'}")
    else:
        lines.append("no layer shows a change between the two dates")
    return "\n".join(lines)


def _bisect_text(d: dict[str, Any]) -> str:
    def where(v: dict[str, Any]) -> str:
        return f"{v['business_date'] or '(no business date)'}  content id {v['content_id'][:12]}"

    lines = [
        f"first bad version: {where(d['first_bad'])}",
        f"last good version: {where(d['last_good'])}",
    ]
    if d["changes"]:
        lines.append("changes between them:")
        lines += [f"  {_change_line(c)}" for c in d["changes"]]
    shown = d.get("changes_vs_good") or d.get("violations") or []
    if shown and not d["changes"]:
        lines.append("against the good version:" if "changes_vs_good" in d else "contract:")
        lines += [
            f"  {_change_line(c)}"
            if "kind" in c
            else f"  {c.get('column')}: {c.get('rule')} expected {_short(c.get('expected'))} "
            f"observed {_short(c.get('observed'))}"
            for c in shown
        ]
    cost = d["cost"]
    limit = "every candidate" if d["mode"] == "verify-all" else f"at most {d['max_evaluations']}"
    lines.append(
        f"tested {d['evaluated']} version(s) of {d['candidates']} candidate(s) ({limit}); "
        f"read {cost['versions_read']} profile(s)"
    )
    lines += [f"warning: {w}" for w in d["warnings"]]
    if d["flips"]:
        lines.append(
            "flipped back to good: "
            + ", ".join(f["business_date"] or f["content_id"][:12] for f in d["flips"])
        )
    return "\n".join(lines)


def _policy(a: argparse.Namespace) -> dict[str, Any]:
    from shape.cli.main import _diff_options

    options: dict[str, Any] = _diff_options(a)  # type: ignore[no-untyped-call]
    return options


def _project(a: argparse.Namespace, hint: str) -> dict[str, Any]:
    from shape.cli import project as project_cli

    ctx = project_cli.context(a, hint)
    return {
        "project": ctx.project if ctx else None,
        "source": ctx.source.name if ctx and ctx.source else None,
    }


def run(a: argparse.Namespace) -> int:
    if a.cmd == "bisect":
        return _bisect(a)
    return _timelapse(a)


def _bisect(a: argparse.Namespace) -> int:
    from shape.versions import bisect

    result = bisect(
        a.registry,
        a.name,
        good=a.good,
        bad=a.bad,
        column=a.column,
        kind=a.kind,
        contract=a.contract,
        verify_all=a.verify_all,
        coarse=a.coarse,
        **_project(a, a.name),
        **_policy(a),
    )
    if a.json:
        _dump(result.to_dict())
    else:
        print(_bisect_text(result.to_dict()))
    return 0


def _timelapse(a: argparse.Namespace) -> int:
    from shape.versions import timelapse
    from shape.versions.timelapse import render_html, render_text

    result = timelapse(
        a.registry,
        a.name,
        column=a.column,
        table=a.table,
        since=a.since,
        until=a.until,
        window=a.window,
        **_project(a, a.name),
        **_policy(a),
    )
    doc = result.to_dict()
    if a.output:
        out = Path(a.output)
        suffix = out.suffix.lower()
        if suffix == ".html":
            text = render_html(doc)
        elif suffix == ".json":
            text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
        elif a.format == "text":
            text = render_text(doc) + "\n"
        else:
            raise ValueError(f"-o {a.output}: the file must end in .json or .html")
        out.write_text(text, encoding="utf-8", newline="\n")
        _dump(
            {
                "written": str(out),
                "frames": len(doc["frames"]),
                "change_points": doc["change_points"],
            }
        )
        return 0
    if a.format == "text":
        print(render_text(doc))
    else:
        _dump(doc)
    return 0
