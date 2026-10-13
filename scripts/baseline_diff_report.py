"""Report how two recorded Shape baseline stores differ for chosen domains (REL-092).

The re-record after a TestPyPI publish writes a new store of generation recordings
(shaped like ``benchmarks/regression/recorded``: ``MANIFEST.json`` and
``generate/<domain>_<scale>.json``).
Healthcare and capital-markets output changes in 0.9.2 on purpose (synthetic procedure and
classification labels), so the Owner reviews exactly those diffs before PyPI. This script prints a
Markdown report: per domain and scale, per seed, per table, whether rows, columns, dataset id and
Parquet bytes changed, and whether the identifier forms changed. Exit status 0 always means the
report was produced; use ``--fail-on-unexpected`` to exit 1 when a domain outside ``--domains``
changed (a domain that should be unchanged).

    python scripts/baseline_diff_report.py OLD_STORE NEW_STORE
    python scripts/baseline_diff_report.py OLD NEW --domains healthcare capital_markets \
        --out diff.md

Standard library only. Every file is checked against its store's manifest digest before use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_DOMAINS = ("healthcare", "capital_markets")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_store(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The manifest and every ``generate/*.json`` record, each verified against the manifest."""
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{root}: no readable MANIFEST.json ({exc.__class__.__name__})") from None
    records: dict[str, dict[str, Any]] = {}
    for name, entry in sorted(manifest.get("files", {}).items()):
        if not name.startswith("generate/"):
            continue
        path = root / name
        if not path.is_file() or _sha256(path) != entry.get("sha256"):
            raise SystemExit(f"{root}/{name}: missing or digest mismatch with MANIFEST.json")
        records[json.loads(path.read_text(encoding="utf-8"))["workload"]] = json.loads(
            path.read_text(encoding="utf-8")
        )
    return manifest, records


def _domain(workload: str) -> str:
    return workload.split(":")[1]


def compare_record(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Human-readable differences between two ``generate:`` records (empty when identical)."""
    out: list[str] = []
    for seed in sorted(set(old["seeds"]) | set(new["seeds"]), key=int):
        a, b = old["seeds"].get(seed), new["seeds"].get(seed)
        if a is None or b is None:
            out.append(f"seed {seed}: present only in {'old' if b is None else 'new'}")
            continue
        for table in sorted(set(a["tables"]) | set(b["tables"])):
            ta, tb = a["tables"].get(table), b["tables"].get(table)
            if ta is None or tb is None:
                out.append(f"seed {seed} `{table}`: table only in {'old' if tb is None else 'new'}")
                continue
            changed = [
                k
                for k in ("rows", "columns", "dataset_id", "parquet_sha256", "parquet_bytes")
                if ta[k] != tb[k]
            ]
            if changed:
                detail = ", ".join(
                    f"{k} {ta[k]} -> {tb[k]}" if k in ("rows", "parquet_bytes") else k
                    for k in changed
                )
                out.append(f"seed {seed} `{table}`: {detail}")
    if old.get("identifier_forms") != new.get("identifier_forms"):
        out.append("identifier forms changed")
    return out


def report(old_root: Path, new_root: Path, domains: tuple[str, ...]) -> tuple[str, set[str]]:
    old_m, old = load_store(old_root)
    new_m, new = load_store(new_root)
    lines = [
        "# Baseline diff",
        "",
        f"- old: Shape {old_m.get('shape_version')} (commit `{old_m.get('shape_commit')}`), "
        f"recorded {old_m.get('recorded_utc')}",
        f"- new: Shape {new_m.get('shape_version')} (commit `{new_m.get('shape_commit')}`), "
        f"recorded {new_m.get('recorded_utc')}",
        "",
    ]
    changed_domains: set[str] = set()
    for title, selected in (
        ("Domains under review", [d for d in domains]),
        (
            "All other domains (expected unchanged)",
            sorted({_domain(w) for w in {*old, *new}} - set(domains)),
        ),
    ):
        lines += [f"## {title}", ""]
        for domain in selected:
            workloads = sorted(w for w in {*old, *new} if _domain(w) == domain)
            if not workloads:
                lines.append(f"- **{domain}**: no recording in either store")
                continue
            diffs = {}
            for w in workloads:
                if w not in old or w not in new:
                    diffs[w] = [f"recorded only in {'new' if w in new else 'old'} store"]
                else:
                    diffs[w] = compare_record(old[w], new[w])
            if any(diffs.values()):
                changed_domains.add(domain)
            lines.append(f"### {domain}: {'CHANGED' if domain in changed_domains else 'unchanged'}")
            lines.append("")
            for w, items in diffs.items():
                lines.append(
                    f"- `{w}`: {'identical' if not items else f'{len(items)} difference(s)'}"
                )
                lines += [f"    - {item}" for item in items[:40]]
                if len(items) > 40:
                    lines.append(f"    - ... {len(items) - 40} more")
            lines.append("")
    return "\n".join(lines) + "\n", changed_domains


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--domains", nargs="+", default=list(DEFAULT_DOMAINS))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--fail-on-unexpected", action="store_true")
    a = ap.parse_args(argv)
    text, changed = report(a.old, a.new, tuple(a.domains))
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if a.fail_on_unexpected and changed - set(a.domains) else 0


if __name__ == "__main__":
    raise SystemExit(main())
