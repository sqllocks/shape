"""Shape-plus-vault generation (W5-03, item 7).

``shape generate --from X.shape`` is shape-only: statistical, from what the profile holds. With a
vault, a column the policy gave ``categories`` draws from the exact category values and
frequencies in the vault, a column with ``extremes`` uses the raw minimum and maximum as bounds,
and every other column is generated as in shape-only mode. The vault is checked against the
profile first (:func:`shape.vault.ops.verify_vault`) and refused when it does not match.

The values are placed into the fitted schema (:func:`shape.generation.fit.fit_schema`'s ``vault``
hook), so the rest of generation is unchanged and without a vault nothing here runs.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shape.generation.fidelity import PlanItem

from .errors import VaultReferenceError
from .format import VaultColumn, decode_value, open_vault
from .kek import resolve_kek
from .ops import read_vault_bytes, verify_vault

MODE_SHAPE = "shape"
MODE_VAULT = "shape+vault"
STATUS = "vault"
WARNING = (
    "shape: warning: output generated with {vault} contains real values from the vault; "
    "treat it like the source data"
)

# Strategies a vaulted column may replace or bound: keys, temporal and joint columns keep theirs.
_CATEGORY_STRATEGIES = frozenset({"weighted_enum", "faker", "distribution", "empirical"})
_NUMERIC = frozenset({"integer", "float", "decimal"})
_TEMPORAL = frozenset({"date", "datetime", "timestamp"})


@dataclass(frozen=True)
class VaultRun:
    """A vault that matched its profile and was opened for one generation."""

    vault_id: str
    columns: dict[str, VaultColumn]

    @property
    def vaulted_columns(self) -> list[str]:
        return sorted(self.columns)


def open_for_generation(
    vault_path: str | os.PathLike[str],
    shape_path: str | os.PathLike[str],
    kek: str,
    *,
    verify_key: bytes | None = None,
) -> VaultRun:
    """Check the vault against the profile (hash, ``vault_id``, ``profile_content_id``, the
    signature when ``verify_key`` is given, and every column's authentication) and open it.
    Anything that does not match is a :class:`VaultReferenceError` (exit 1): nothing is
    generated."""
    key = resolve_kek(kek)
    report = verify_vault(vault_path, shape_path, kek=key, verify_key=verify_key)
    bad = [c for c in report["checks"] if not c["ok"]]
    if bad:
        raise VaultReferenceError(
            "the vault does not match the profile: "
            + "; ".join(f"{c['check']}: {c['detail']}" for c in bad)
        )
    opened = open_vault(read_vault_bytes(vault_path), key)
    return VaultRun(opened.vault_id, dict(opened.columns))


def _weights(payload: dict[str, Any]) -> dict[str, float] | None:
    for surface in ("enum_values", "value_counts_ext"):
        rows = payload.get(surface)
        if isinstance(rows, list) and rows:
            weights = {str(v): float(n) for v, n in rows if n > 0}
            if weights:
                return weights
    return None


def _bound(payload: dict[str, Any], key: str) -> Any:
    tagged = payload.get(key)
    if isinstance(tagged, list) and len(tagged) == 2:
        return decode_value(tagged[1])
    return None


def overlay(run: VaultRun) -> Callable[[dict[str, Any]], list[PlanItem]]:
    """The ``vault`` hook of ``fit_schema``: puts the vault's values into the schema document and
    returns the plan items (status ``vault``) that replace the profile's own for those fields."""

    def apply(doc: dict[str, Any]) -> list[PlanItem]:
        items: list[PlanItem] = []
        correlated = {
            (t, c)
            for t, pairs in (doc.get("correlated_columns") or {}).items()
            for pair in pairs
            for c in pair[:2]
        }
        for column, entry in sorted(run.columns.items()):
            table, _, cname = column.partition(".")
            cols = (doc.get("tables", {}).get(table) or {}).get("columns") or {}
            col = cols.get(cname)
            if col is None:
                tname, _, rest = column.rpartition(".")
                col = (doc.get("tables", {}).get(tname) or {}).get("columns", {}).get(rest)
                table, cname = tname, rest
            if col is None:
                raise VaultReferenceError(f"the vault holds {column}, which the profile lacks")
            gen = dict(col.get("generator") or {})
            strategy = gen.get("strategy")
            payload = entry.payload
            dtype = str(payload.get("dtype") or "")
            weights = _weights(payload)
            if (
                entry.policy in ("categories", "all")
                and weights is not None
                and strategy in _CATEGORY_STRATEGIES
                and (table, cname) not in correlated
                and dtype not in _TEMPORAL
            ):
                gen = {"strategy": "weighted_enum", "values": weights}
                if dtype == "integer":
                    gen["output_type"] = "int64"
                elif dtype == "boolean":
                    gen["output_type"] = "bool"
                    gen["values"] = {k.lower(): v for k, v in weights.items()}
                col["generator"] = gen
                for surface in ("enum_values", "value_counts_ext"):
                    items.append(
                        PlanItem(
                            f"{table}.{cname}.{surface}",
                            STATUS,
                            "generated from the exact categories and frequencies in the vault",
                        )
                    )
                continue
            low, high = _bound(payload, "min"), _bound(payload, "max")
            if entry.policy in ("extremes", "all") and (low is not None or high is not None):
                if strategy in ("distribution", "empirical") and dtype in _NUMERIC:
                    target = gen["params"] if strategy == "distribution" else gen
                    target = dict(target)
                    if low is not None:
                        target["min"] = float(low)
                    if high is not None:
                        target["max"] = float(high)
                    if strategy == "distribution":
                        gen["params"] = target
                    else:
                        gen = target
                elif strategy == "temporal":
                    if low is not None:
                        gen["start"] = str(low)
                    if high is not None:
                        gen["end"] = str(high)
                else:
                    continue
                col["generator"] = gen
                for surface, value in (("min_value", low), ("max_value", high)):
                    if value is not None:
                        items.append(
                            PlanItem(
                                f"{table}.{cname}.{surface}",
                                STATUS,
                                "bounded by the raw minimum and maximum in the vault",
                            )
                        )
        return items

    return apply
