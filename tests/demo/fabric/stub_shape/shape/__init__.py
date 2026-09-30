"""TEST-ONLY local stand-in for the section 12.2 `shape` API.

Lane L1 builds the real API. Until it lands, the L2 tests import this stub instead
(see ``tests/demo/fabric/conftest.py``). It implements just enough of ``profile``,
``save``/``load``, ``check`` and ``diff`` for the Fabric integration tests. It is
never shipped and never imported by anything under ``integrations/`` except through
the normal ``import shape``.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

STUB = True
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CATEGORICAL_MAX = 50


def _to_frame(source: Any) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        return source
    if isinstance(source, pa.Table):
        return source.to_pandas()
    p = Path(str(source))
    if p.is_dir() and (p / "_delta_log").exists():
        from deltalake import DeltaTable

        return DeltaTable(str(p)).to_pyarrow_table().to_pandas()
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    if p.suffix == ".csv":
        return pd.read_csv(p)
    if p.suffix == ".jsonl":
        return pd.read_json(p, lines=True)
    raise ValueError(f"unsupported source: {source!r}")


def _dtype(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    return "string"


def _column(s: pd.Series) -> dict[str, Any]:
    n = len(s)
    nn = s.dropna()
    dt = _dtype(s)
    card = int(nn.nunique())
    col: dict[str, Any] = {
        "dtype": dt,
        "null_rate": float((n - len(nn)) / n) if n else 0.0,
        "cardinality": card,
        "is_unique": bool(n > 0 and card == n),
        "is_primary_key": False,
        "is_foreign_key": False,
        "fk_ref_table": None,
        "distribution": None,
        "pattern": None,
        "min": None,
        "max": None,
        "mean": None,
        "std": None,
        "values": None,
    }
    if dt in ("integer", "float") and len(nn):
        col.update(min=float(nn.min()), max=float(nn.max()), mean=float(nn.mean()))
        col["std"] = float(nn.std(ddof=0))
        skew = float(((nn - nn.mean()) ** 3).mean() / (col["std"] ** 3)) if col["std"] else 0.0
        col["distribution"] = "normal" if abs(skew) < 0.5 else "log_normal"
    elif dt == "string" and len(nn):
        strs = nn.astype(str)
        if strs.map(lambda v: bool(_EMAIL.match(v))).all():
            col["pattern"] = "email"
        if card <= _CATEGORICAL_MAX:
            col["values"] = sorted(strs.unique().tolist())
    col["is_primary_key"] = bool(col["is_unique"] and col["null_rate"] == 0.0 and dt != "float")
    return col


class Profile:
    def __init__(self, data: dict[str, Any]):
        self._d = data

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._d))

    def summary(self) -> dict[str, Any]:
        d = self.to_dict()
        for c in d["columns"].values():
            c.pop("values", None)
        return d

    def to_html(self) -> str:
        rows = "".join(
            f"<tr><td>{n}</td><td>{c['dtype']}</td><td>{c['null_rate']:.3f}</td>"
            f"<td>{c['cardinality']}</td></tr>"
            for n, c in self._d["columns"].items()
        )
        return (
            f"<html><body><h1>{self._d['name']} ({self._d['row_count']} rows)</h1>"
            f"<table><tr><th>column</th><th>dtype</th><th>null_rate</th><th>cardinality</th></tr>"
            f"{rows}</table></body></html>"
        )


def profile(source: Any, *, name: str | None = None) -> Profile:
    df = _to_frame(source)
    return Profile(
        {
            "name": name or "table",
            "row_count": int(len(df)),
            "columns": {str(c): _column(df[c]) for c in df.columns},
        }
    )


def _enc(o: Any) -> Any:
    if isinstance(o, float) and not math.isfinite(o):
        return {"__float__": repr(o)}
    if isinstance(o, dict):
        return {k: _enc(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_enc(v) for v in o]
    return o


def _dec(o: Any) -> Any:
    if isinstance(o, dict):
        if set(o) == {"__float__"}:
            return float(o["__float__"])
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


def save(p: Profile, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(_enc(p._d), allow_nan=False))


def load(path: str | Path) -> Profile:
    return Profile(_dec(json.loads(Path(path).read_text())))


class CheckResult:
    def __init__(self, violations: list[dict[str, Any]]):
        self.violations = violations
        self.passed = not violations

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "violations": self.violations}


def check(p: Profile, contract: dict[str, Any] | str | Path) -> CheckResult:
    if not isinstance(contract, dict):
        contract = json.loads(Path(contract).read_text())
    d = p._d
    v: list[dict[str, Any]] = []

    def bad(column: str | None, rule: str, expected: Any, observed: Any) -> None:
        v.append({"column": column, "rule": rule, "expected": expected, "observed": observed})

    rc = contract.get("row_count", {})
    if "min" in rc and d["row_count"] < rc["min"]:
        bad(None, "row_count.min", rc["min"], d["row_count"])
    if "max" in rc and d["row_count"] > rc["max"]:
        bad(None, "row_count.max", rc["max"], d["row_count"])
    for name in contract.get("required_columns", []):
        if name not in d["columns"]:
            bad(name, "required_columns", "present", "missing")
    if not contract.get("allow_extra_columns", True):
        for name in d["columns"]:
            if name not in contract.get("columns", {}):
                bad(name, "allow_extra_columns", False, "extra column")
    for name, rules in contract.get("columns", {}).items():
        c = d["columns"].get(name)
        if c is None:
            bad(name, "column_exists", "present", "missing")
            continue
        if "dtype" in rules and c["dtype"] != rules["dtype"]:
            bad(name, "dtype", rules["dtype"], c["dtype"])
        if rules.get("nullable") is False and c["null_rate"] > 0:
            bad(name, "nullable", False, c["null_rate"])
        if rules.get("unique") and not c["is_unique"]:
            bad(name, "unique", True, False)
        if "max_null_rate" in rules and c["null_rate"] > rules["max_null_rate"]:
            bad(name, "max_null_rate", rules["max_null_rate"], c["null_rate"])
        if "pattern" in rules and c["pattern"] != rules["pattern"]:
            bad(name, "pattern", rules["pattern"], c["pattern"])
        if "allowed_values" in rules:
            extra = sorted(set(c.get("values") or []) - set(rules["allowed_values"]))
            if extra:
                bad(name, "allowed_values", rules["allowed_values"], extra)
        if "min" in rules and c["min"] is not None and c["min"] < rules["min"]:
            bad(name, "min", rules["min"], c["min"])
        if "max" in rules and c["max"] is not None and c["max"] > rules["max"]:
            bad(name, "max", rules["max"], c["max"])
        if "distribution" in rules and c["distribution"] != rules["distribution"]:
            bad(name, "distribution", rules["distribution"], c["distribution"])
    return CheckResult(v)


class DiffResult:
    def __init__(self, changes: list[dict[str, Any]]):
        self.changes = changes
        self.drifted = bool(changes)

    def to_dict(self) -> dict[str, Any]:
        return {"drifted": self.drifted, "changes": self.changes}


def diff(
    baseline: Profile, current: Profile, *, thresholds: dict[str, Any] | None = None
) -> DiffResult:
    b, c = baseline._d["columns"], current._d["columns"]
    out: list[dict[str, Any]] = []

    def add(column: str, kind: str, bv: Any, cv: Any, sev: str) -> None:
        out.append({"column": column, "kind": kind, "baseline": bv, "current": cv, "severity": sev})

    for n in sorted(set(b) | set(c)):
        if n not in c:
            add(n, "column_removed", "present", None, "high")
        elif n not in b:
            add(n, "column_added", None, "present", "high")
        else:
            x, y = b[n], c[n]
            if x["dtype"] != y["dtype"]:
                add(n, "dtype_change", x["dtype"], y["dtype"], "high")
            if abs(y["null_rate"] - x["null_rate"]) > 0.05:
                add(n, "null_rate_change", x["null_rate"], y["null_rate"], "medium")
            if x["cardinality"] and not (0.67 <= y["cardinality"] / x["cardinality"] <= 1.5):
                add(n, "cardinality_change", x["cardinality"], y["cardinality"], "medium")
            if x["std"] and abs((y["mean"] or 0) - (x["mean"] or 0)) > 0.5 * x["std"]:
                add(n, "mean_shift", x["mean"], y["mean"], "medium")
            if x["distribution"] != y["distribution"]:
                add(n, "distribution_change", x["distribution"], y["distribution"], "low")
            new = sorted(set(y.get("values") or []) - set(x.get("values") or []))
            if new and x.get("values") is not None:
                add(n, "new_values", None, new, "low")
    return DiffResult(out)


__all__ = ["profile", "save", "load", "check", "diff", "Profile"]
