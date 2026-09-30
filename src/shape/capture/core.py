"""Bounded single-pass capture."""

from dataclasses import dataclass

from shape.profile.error import hll_error, kll_error
from shape.profile.infer import TypeTracker, as_number, is_number
from shape.profile.numeric import NumericProfile
from shape.profile.text import TextProfile


@dataclass(frozen=True, slots=True)
class CapturedShape:
    rows: int
    columns: dict

    def to_dict(self):
        return {"rows": self.rows, "columns": self.columns}


def capture_rows(rows, batch_size=10000):
    """One bounded pass over row dicts. The type of a column is decided at the end, so a
    leading null (P3), numpy or Decimal numbers (P4) and a late text value (P2) cannot change
    or lose what was seen: every value is recorded as text, and as a number while the column
    is still all-numeric."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    numeric: dict[str, NumericProfile] = {}
    text: dict[str, TextProfile] = {}
    types: dict[str, TypeTracker] = {}
    count = 0
    for raw in rows:
        row = dict(raw)
        count += 1
        for key in types:
            if key not in row:
                _observe(key, None, numeric, text, types)
        for key, v in row.items():
            if key not in types:
                numeric[key], text[key], types[key] = NumericProfile(), TextProfile(), TypeTracker()
                for _ in range(count - 1):  # rows before the column first appeared
                    _observe(key, None, numeric, text, types)
            _observe(key, v, numeric, text, types)
    profiles = {k: (numeric[k] if types[k].kind == "numeric" else text[k]) for k in types}
    kinds = {k: types[k].kind for k in types}
    cols = {}
    for k, p in profiles.items():
        if kinds[k] == "numeric":
            cols[k] = {
                "kind": "numeric",
                **p.summary(),
                "error_models": {
                    "cardinality": hll_error(p.cardinality.p).to_dict(),
                    "quantiles": kll_error(p.quantiles.k).to_dict(),
                },
            }
        else:
            cols[k] = {
                "kind": "text",
                "count": p.count,
                "null_count": p.null_count,
                "length": p.lengths.summary(),
                "distinct_estimate": p.cardinality.estimate(),
                "topk": p.topk.top(10),
                "error_models": {"cardinality": hll_error(p.cardinality.p).to_dict()},
            }
    return CapturedShape(count, cols)


def _observe(key, v, numeric, text, types):
    types[key].observe(v)
    text[key].update_value(v)
    # The numeric side only matters while every value so far is a number or null; once a
    # text value has been seen it is never used, so it is simply not fed any more.
    if v is None:
        numeric[key].update_value(None)
    elif is_number(v):
        numeric[key].update_value(as_number(v))
