"""Generate the Unicode table the pure-Python twin of ``string_case`` uses outside ASCII.

The native kernel's ``string_case`` is the contract: ``title`` splits words with Rust's
``char::is_alphanumeric``, ``upper`` and ``lower`` use the Rust standard library's case tables,
and ``lower`` maps a capital sigma to the final form by the Unicode ``Final_Sigma`` rule with the
standard library's ``Cased`` and ``Case_Ignorable`` properties. Python's ``str`` methods use the
running Python's Unicode version and a different word rule, so the twin
(``src/shape/kernel/reference/gen.py``) reads this table instead.

The table is read off the native kernel itself, by probing every code point, so it holds exactly
what the installed extension does. Build the extension with the toolchain the wheels are built
with (``maturin develop --release``), then run from the repo root::

    python scripts/gen_unicode_case_table.py           # rewrite the table
    python scripts/gen_unicode_case_table.py --check   # exit 1 if the table is stale

``tests/kernel/test_unicode_case.py`` fails when the shipped table and the native kernel disagree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "src" / "shape" / "kernel" / "reference" / "unicode_case.json"
FORMAT = 1
SIGMA = "Σ"
ALPHA = "Α"  # GREEK CAPITAL LETTER ALPHA: cased, not case-ignorable


def code_points() -> list[int]:
    """Every Unicode scalar value (surrogates cannot occur in UTF-8 text)."""
    return [c for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]


def _ranges(cps: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for c in cps:
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def build_table(kernel: Any) -> dict[str, Any]:
    """Probe ``kernel.string_case`` on every code point and return the table."""
    import pyarrow as pa

    cps = code_points()
    chars = [chr(c) for c in cps]

    def run(values: list[str], mode: str) -> list[str]:
        return pa.array(kernel.string_case(pa.array(values, pa.string()), mode)).to_pylist()

    upper = run(chars, "upper")
    lower = run(chars, "lower")
    # title of c + "a": a word character keeps the "a" inside its word ("Ca"), any other
    # character ends the word, so the "a" starts a new one ("cA").
    title = run([c + "a" for c in chars], "title")
    # Final_Sigma probes: Σ is final when a cased letter precedes it (skipping case-ignorable
    # characters) and none follows. Only the first character that is not case-ignorable is
    # looked at, so each code point is one of three classes: case-ignorable, cased, other.
    sig_after = run([c + SIGMA for c in chars], "lower")
    sig_between = run([ALPHA + c + SIGMA for c in chars], "lower")
    sig_before = run([ALPHA + SIGMA + c for c in chars], "lower")

    alnum: list[int] = []
    cased: list[int] = []
    ignorable: list[int] = []
    upper_map: list[list[int]] = []
    lower_map: list[list[int]] = []
    for i, c in enumerate(cps):
        ch = chars[i]
        u, lo = upper[i], lower[i]
        word = title[i] == u + "a"
        if not word and title[i] != lo + "A":
            raise SystemExit(f"U+{c:04X}: unexpected title result {title[i]!r}")
        is_cased = sig_after[i].endswith("ς")
        is_ignorable = sig_between[i].endswith("ς") and not is_cased
        final_before = sig_before[i][1] == "ς"
        if final_before != (not is_cased):
            raise SystemExit(f"U+{c:04X}: inconsistent Final_Sigma probes")
        if is_cased:
            cased.append(c)
        if is_ignorable:
            ignorable.append(c)
        if c < 0x80:
            # ASCII never reaches the table for case mapping or word splitting; check that the
            # kernel treats it as the twin's ASCII path does.
            if (u, lo, word) != (ch.upper(), ch.lower(), ch.isalnum()):
                raise SystemExit(f"U+{c:04X}: ASCII case differs from the ASCII rule")
            continue
        if word:
            alnum.append(c)
        if u != ch:
            upper_map.append([c, *map(ord, u)])
        if lo != ch:
            lower_map.append([c, *map(ord, lo)])
    return {
        "format": FORMAT,
        "alnum": _ranges(alnum),
        "cased": _ranges(cased),
        "case_ignorable": _ranges(ignorable),
        "upper": upper_map,
        "lower": lower_map,
    }


def dumps(table: dict[str, Any]) -> str:
    """Stable text: one range or mapping per line, so a regenerated table diffs cleanly."""
    lines = ["{", f'  "format": {table["format"]},']
    keys = ["alnum", "cased", "case_ignorable", "upper", "lower"]
    for k, key in enumerate(keys):
        rows = [json.dumps(r, separators=(",", ":")) for r in table[key]]
        body = ",\n    ".join(rows)
        end = "," if k < len(keys) - 1 else ""
        lines.append(f'  "{key}": [\n    {body}\n  ]{end}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the table is stale")
    args = parser.parse_args(argv)
    import shape._kernel as kernel  # the installed native extension

    text = dumps(build_table(kernel))
    if args.check:
        if TABLE.read_text(encoding="utf-8") != text:
            print(f"{TABLE.relative_to(ROOT)} is stale; regenerate it", file=sys.stderr)
            return 1
        print(f"{TABLE.relative_to(ROOT)} is up to date")
        return 0
    TABLE.write_text(text, encoding="utf-8")
    print(f"wrote {TABLE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
