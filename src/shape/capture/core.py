"""Bounded single-pass capture."""

from dataclasses import dataclass

from shape.profile.error import hll_error, kll_error
from shape.profile.numeric import NumericProfile
from shape.profile.text import TextProfile


@dataclass(frozen=True, slots=True)
class CapturedShape:
    rows: int
    columns: dict

    def to_dict(self):
        return {"rows": self.rows, "columns": self.columns}


def _kind(v):
    return "numeric" if isinstance(v, (int, float)) and not isinstance(v, bool) else "text"


def capture_rows(rows, batch_size=10000):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    profiles = {}
    kinds = {}
    count = 0
    for raw in rows:
        row = dict(raw)
        count += 1
        for key, p in list(profiles.items()):
            if key not in row:
                p.update_value(None)
        for key, v in row.items():
            if key not in profiles:
                kinds[key] = _kind(v) if v is not None else "text"
                profiles[key] = NumericProfile() if kinds[key] == "numeric" else TextProfile()
                if count > 1:
                    profiles[key].update([None] * (count - 1))
            p = profiles[key]
            if (
                kinds[key] == "numeric"
                and v is not None
                and not (isinstance(v, (int, float)) and not isinstance(v, bool))
            ):
                old = p
                np = TextProfile()
                np.count = old.count - 1
                np.null_count = old.null_count
                profiles[key] = p = np
                kinds[key] = "text"
            p.update_value(v)
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
