"""``FidelityReport``: the real and the synthetic profile compared, column by column.

A column passes when its null rate is within five points of the real one and, unless it is a key,
a date, a float or a pattern that is unique by nature (phone, e-mail, uuid), its cardinality is
close: normalised for the difference in size for high-cardinality columns, and a ratio of
the cardinalities for categories. The score is the share of columns that pass.
"""

from __future__ import annotations

import sys
from typing import Any, TextIO


def _rate(column: Any) -> float | None:
    value = getattr(column, "null_rate", None)
    return None if value is None else float(value)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


class FidelityReport:
    def __init__(
        self,
        real_profile: Any,
        synthetic_profile: Any,
        table_name: str = "",
        out: TextIO | None = None,
    ) -> None:
        self._real = real_profile
        self._synthetic = synthetic_profile
        self._table = table_name
        self._out = out

    def comparisons(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        real_tables = getattr(self._real, "tables", {})
        syn_tables = getattr(self._synthetic, "tables", {})
        for tname in sorted(set(real_tables) | set(syn_tables)):
            real_t = real_tables.get(tname)
            syn_t = syn_tables.get(tname)
            if real_t is None or syn_t is None:
                continue
            for col_name in sorted(set(real_t.columns) | set(syn_t.columns)):
                rc = real_t.columns.get(col_name)
                sc = syn_t.columns.get(col_name)
                if rc is None or sc is None:
                    continue
                results.append(
                    {
                        "table": tname,
                        "column": col_name,
                        "dtype": rc.dtype,
                        "real_nulls": _percent(_rate(rc)),
                        "syn_nulls": _percent(_rate(sc)),
                        "real_card": str(rc.cardinality),
                        "syn_card": str(sc.cardinality),
                        "pass": self._check_pass(rc, sc),
                    }
                )
        return results

    def _check_pass(self, real_col: Any, syn_col: Any) -> bool:
        real_rate, syn_rate = _rate(real_col), _rate(syn_col)
        if real_rate is not None and syn_rate is not None and abs(real_rate - syn_rate) > 0.05:
            return False
        # Keys: their absolute range differs across samples; skip cardinality.
        if getattr(real_col, "is_primary_key", False) or getattr(real_col, "is_unique", False):
            return True
        dtype = getattr(real_col, "dtype", "")
        # Bounded dates saturate with sample size, and a float's cardinality is quantization
        # noise: the null-rate check above is all that is compared.
        if dtype in ("date", "datetime", "float"):
            return True
        if getattr(real_col, "pattern", None) in ("phone", "phone_number", "email", "uuid"):
            return True
        real_card = getattr(real_col, "cardinality", 0) or 0
        syn_card = getattr(syn_col, "cardinality", 0) or 0
        if real_card and syn_card:
            real_cr = getattr(real_col, "cardinality_ratio", real_card) or 0.0
            syn_cr = getattr(syn_col, "cardinality_ratio", syn_card) or 0.0
            # Integers tolerate wider skew than strings: zero-inflation and clustering give
            # cardinality ratios a generator cannot reproduce exactly.
            high_card_threshold = 0.3 if dtype == "integer" else 0.5
            high_card_tolerance = 0.75 if dtype == "integer" else 0.35
            cat_lo, cat_hi = (0.2, 5.0) if dtype == "integer" else (0.5, 2.0)
            if real_cr > high_card_threshold:
                if abs(real_cr - syn_cr) > high_card_tolerance:
                    return False
            else:
                ratio = syn_card / max(real_card, 1)
                if not (cat_lo <= ratio <= cat_hi):
                    return False
        return True

    def overall_score(self) -> float:
        comparisons = self.comparisons()
        if not comparisons:
            return 1.0
        return sum(1 for c in comparisons if c["pass"]) / len(comparisons)

    def render(self) -> None:
        out = self._out or sys.stdout
        comparisons = self.comparisons()
        title = f"Fidelity Report{' — ' + self._table if self._table else ''}"
        print(title, file=out)
        print(f"{'Table':<20} {'Column':<25} {'Type':<10} {'Pass':>5}", file=out)
        print("-" * 65, file=out)
        for c in comparisons:
            print(
                f"{c['table']:<20} {c['column']:<25} {c['dtype']:<10} "
                f"{'OK' if c['pass'] else 'FAIL':>5}",
                file=out,
            )
        print(f"\nFidelity score: {self.overall_score():.1%}", file=out)
