"""Bring-your-own loaders: read a licensed code set the user supplies, never ship or download it.

``load_byo(system, path)`` reads a delimited file (CSV, tab or pipe separated, header row) or a
ClaML XML file (the format WHO and BfArM distribute ICD-10 in), maps it to the common code model
(:mod:`shape_healthcare_codes.model`), and writes the asset to the user data directory, where
``load(system)`` finds it. The manifest records the SHA-256 of the file and ``"byo": true``.

For a delimited file the columns are found by name (case-insensitive), or named in ``columns``
(``{"code": "Code", "long_desc": "Definition"}``). Every column that is not mapped is kept as a
string. Known layouts: NUCC (``Code``, ``Classification``, ``Definition``, ``Display Name``),
CPT/HCPCS-style (``code``, ``long description``, ``short description``), and generic
(``code``, ``description``).

The risk-adjustment tables (``hcc_hierarchy``, ``hcc_coefficients``) are free to fetch, but a
user who already has the files (an offline machine, a later CMS release) loads them here too:
either the CMS model software zip as published (read by the same parser as ``fetch``), or a
delimited file with the table's own columns (:data:`TABLE_COLUMNS`; ``drops`` and
``payment_years`` are lists separated by blanks, ``;`` or ``|``).
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape_healthcare_codes._xml import parse_xml
from shape_healthcare_codes.model import normalize_code
from shape_healthcare_codes.store import write_asset

_CODE = ("code", "icd10", "icd-10", "icd10_code", "cpt", "cpt_code", "hcpcs", "concept_id", "id")
_LONG = (
    "long_desc",
    "long description",
    "long_description",
    "description",
    "definition",
    "descriptor",
    "name",
    "term",
    "label",
    "display",
    "fsn",
)
_SHORT = (
    "short_desc",
    "short description",
    "short_description",
    "display name",
    "classification",
    "title",
)
_FROM = ("valid_from", "effective", "effective date", "start date", "effective_date")
_TO = ("valid_to", "end date", "termination date", "end_date", "expiration date")
_LEAF = ("leaf", "billable", "is_billable", "reportable")


class ByoError(ValueError):
    """The supplied file cannot be read as a code set."""


TABLE_COLUMNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "hcc_hierarchy": (("model", "hcc", "drops"), ()),
    "hcc_coefficients": (
        ("model", "segment", "variable", "coefficient"),
        ("payment_years", "software"),
    ),
}
"""Table asset -> (required columns, optional columns) of its delimited bring-your-own file."""

_LIST = re.compile(r"[\s,;|]+")


def _split(v: str) -> list[str]:
    return [x for x in _LIST.split(v.strip()) if x]


def read_table_delimited(system: str, text: str) -> pa.Table:
    """A delimited ``hcc_hierarchy`` or ``hcc_coefficients`` file as the table ``fetch`` builds."""
    from shape_healthcare_codes.builders import hcc_coefficients, hcc_hierarchy

    required, optional = TABLE_COLUMNS[system]
    text = text.lstrip("\ufeff")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=_sniff(text)))
    if not rows:
        raise ByoError("the file has no data rows")
    names = {n.strip().lower(): n for n in rows[0] if n}
    missing = [c for c in required if c not in names]
    if missing:
        raise ByoError(f"{system}: missing columns {missing}; needs {list(required + optional)}")
    out: dict[str, list[Any]] = {}
    for i, r in enumerate(rows, 2):
        row = {c: (r.get(names[c]) or "").strip() for c in required + optional if c in names}
        if not row["model"]:
            raise ByoError(f"line {i}: no model")
        if system == "hcc_hierarchy":
            drops = _split(row["drops"])
            if not row["hcc"].isdigit() or not drops or not all(d.isdigit() for d in drops):
                raise ByoError(f"line {i}: hcc and drops must be category numbers")
            values: dict[str, Any] = {"model": row["model"], "hcc": row["hcc"], "drops": drops}
        else:
            if not hcc_coefficients.DECIMAL.match(row["coefficient"]):
                raise ByoError(f"line {i}: coefficient {row['coefficient']!r} is not a decimal")
            years = _split(row.get("payment_years", ""))
            if not all(y.isdigit() for y in years):
                raise ByoError(f"line {i}: payment_years {years!r} are not years")
            if not row["variable"]:
                raise ByoError(f"line {i}: no variable")
            values = {
                "model": row["model"],
                "segment": row["segment"] or None,
                "variable": row["variable"],
                "coefficient": row["coefficient"],
                "payment_years": [int(y) for y in years],
                "software": row.get("software") or "user supplied",
            }
        for k, v in values.items():
            out.setdefault(k, []).append(v)
    schema = (hcc_hierarchy if system == "hcc_hierarchy" else hcc_coefficients).SCHEMA
    return pa.table(out, schema=schema)


def read_table_file(system: str, raw: bytes, fmt: str = "auto") -> pa.Table:
    """``hcc_hierarchy`` or ``hcc_coefficients`` from the model software zip or a delimited file."""
    from shape_healthcare_codes.builders import _cms_software, hcc_coefficients, hcc_hierarchy

    kind = fmt
    if kind == "auto":
        kind = "zip" if raw[:2] == b"PK" else "delimited"
    if kind == "zip":
        mod = hcc_hierarchy if system == "hcc_hierarchy" else hcc_coefficients
        try:
            return mod.from_packages(_cms_software.read_packages(raw))
        except (ValueError, zipfile.BadZipFile) as exc:
            raise ByoError(str(exc)) from exc
    if kind == "delimited":
        return read_table_delimited(system, raw.decode("utf-8-sig", errors="replace"))
    raise ByoError(f"unknown format {fmt!r} for {system}; use auto, zip or delimited")


def _sniff(text: str) -> str:
    head = text.split("\n", 1)[0]
    counts = {d: head.count(d) for d in (",", "\t", "|", ";")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def _pick(names: list[str], options: tuple[str, ...]) -> str | None:
    low = {n.strip().lower(): n for n in names}
    for o in options:
        if o in low:
            return low[o]
    return None


def _date(v: str) -> dt.date | None:
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "y", "yes", "true", "t", "x")


def read_delimited(text: str, columns: Mapping[str, str] | None = None) -> pa.Table:
    text = text.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=_sniff(text)))
    if not rows:
        raise ByoError("the file has no data rows")
    names = list(rows[0].keys())
    cols = dict(columns or {})
    code_c = cols.get("code") or _pick(names, _CODE)
    long_c = cols.get("long_desc") or _pick(names, _LONG)
    short_c = cols.get("short_desc") or _pick(names, _SHORT)
    if code_c is None or code_c not in names:
        raise ByoError(f"cannot find the code column among {names}; pass columns={{'code': ...}}")
    from_c = cols.get("valid_from") or _pick(names, _FROM)
    to_c = cols.get("valid_to") or _pick(names, _TO)
    leaf_c = cols.get("leaf") or _pick(names, _LEAF)
    mapped = {c for c in (code_c, long_c, short_c, from_c, to_c, leaf_c) if c}
    extra = [n for n in names if n not in mapped and n]
    out: dict[str, list[Any]] = {
        k: [] for k in "code short_desc long_desc leaf valid_from valid_to".split()
    }
    ext: dict[str, list[Any]] = {n: [] for n in extra}
    seen: set[str] = set()
    for r in rows:
        code = normalize_code(r.get(code_c) or "")
        if not code or code in seen:
            continue
        seen.add(code)
        long_d = (r.get(long_c) or "").strip() if long_c else ""
        short_d = (r.get(short_c) or "").strip() if short_c else long_d
        out["code"].append(code)
        out["short_desc"].append(short_d or long_d)
        out["long_desc"].append(long_d or short_d)
        out["leaf"].append(_truthy(r.get(leaf_c) or "") if leaf_c else True)
        out["valid_from"].append(_date(r.get(from_c) or "") if from_c else None)
        out["valid_to"].append(_date(r.get(to_c) or "") if to_c else None)
        for n in extra:
            ext[n].append((r.get(n) or "").strip() or None)
    if not out["code"]:
        raise ByoError("no codes found")
    fields = [
        ("code", pa.string()),
        ("short_desc", pa.string()),
        ("long_desc", pa.string()),
        ("leaf", pa.bool_()),
        ("valid_from", pa.date32()),
        ("valid_to", pa.date32()),
    ]
    arrays: dict[str, Any] = dict(out)
    for n in extra:
        key = n.strip().lower().replace(" ", "_")
        if key in arrays:
            continue
        fields.append((key, pa.string()))
        arrays[key] = ext[n]
    return pa.table(arrays, schema=pa.schema(fields))


def read_claml(xml: bytes) -> pa.Table:
    """A ClaML classification (WHO ICD-10, ICD-10-GM): categories with their preferred label;
    a category with no sub-classes is a leaf. Chapters and blocks are not codes."""
    root = parse_xml(xml)
    cats: dict[str, tuple[str, bool]] = {}
    for cls in root.iter("Class"):
        if cls.get("kind") != "category":
            continue
        code = normalize_code(cls.get("code") or "")
        label = ""
        for rub in cls.findall("Rubric"):
            if rub.get("kind") == "preferred":
                label = "".join(rub.itertext()).strip()
                break
        if code:
            cats[code] = (label, cls.find("SubClass") is None)
    if not cats:
        raise ByoError("no category classes found: is this a ClaML file?")
    codes = sorted(cats)
    return pa.table(
        {
            "code": codes,
            "short_desc": [cats[c][0] for c in codes],
            "long_desc": [cats[c][0] for c in codes],
            "leaf": [cats[c][1] for c in codes],
            "valid_from": pa.array([None] * len(codes), pa.date32()),
            "valid_to": pa.array([None] * len(codes), pa.date32()),
        }
    )


def load_byo(
    system: str,
    path: Path,
    *,
    fmt: str = "auto",
    columns: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> Path:
    """Read ``path`` as ``system`` and write the asset. Returns the written Arrow file."""
    from shape_healthcare_codes.provenance import all_assets

    asset = all_assets().get(system)
    if system in TABLE_COLUMNS and asset is not None:
        return _load_table(system, path, fmt, data_dir)
    if asset is None or asset.mode != "byo":
        raise ByoError(
            f"{system!r} is not a bring-your-own system; bring-your-own systems: "
            f"{sorted(a.id for a in all_assets().values() if a.mode == 'byo')}"
        )
    raw = path.read_bytes()
    kind = fmt
    if kind == "auto":
        kind = "claml" if raw.lstrip()[:5] in (b"<?xml", b"<Clam") else "delimited"
    if kind == "claml":
        table = read_claml(raw)
    elif kind == "delimited":
        table = read_delimited(raw.decode("utf-8-sig", errors="replace"), columns)
    else:
        raise ByoError(f"unknown format {fmt!r}; use auto, delimited or claml")
    manifest = {
        "asset": system,
        "release": "user supplied",
        "byo": True,
        "redistributable": False,
        "source": asset.source_url,
        "licence": asset.licence,
        "sources": {path.name: hashlib.sha256(raw).hexdigest()},
        "subset": False,
    }
    return write_asset(system, table, manifest, data_dir)


def _load_table(system: str, path: Path, fmt: str, data_dir: Path | None) -> Path:
    from shape_healthcare_codes.provenance import all_assets
    from shape_healthcare_codes.store import TABLE_FORMATS

    asset = all_assets()[system]
    raw = path.read_bytes()
    table = read_table_file(system, raw, fmt)
    fmt_name, version = TABLE_FORMATS[system]
    manifest = {
        "asset": system,
        "format": fmt_name,
        "version": version,
        "release": "user supplied",
        "byo": True,
        "redistributable": asset.mode != "byo",
        "source": asset.source_url,
        "licence": asset.licence,
        "sources": {path.name: hashlib.sha256(raw).hexdigest()},
        "subset": False,
    }
    return write_asset(system, table, manifest, data_dir)
