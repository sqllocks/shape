"""What goes into a vault, and writing it with a profile (W5-03, item 4).

The vault holds only what the safe capture withheld. For each column, the policy
(:mod:`shape.vault.policy`) says what to keep; an entry is written only when the safe capture
(its ``redaction_manifest``) actually suppressed that surface of the column:

* ``categories``: the enum values and the top values, with their row counts, when the safe
  capture suppressed or folded the column's categories. The payload is the column's complete
  category table as profiled (``enum_values`` and ``value_counts_ext``), so a vault run can draw
  from the exact categories and frequencies;
* ``extremes``: the raw minimum and maximum, when the safe capture suppressed them;
* ``all``: both; ``none``: nothing.

A column that the safe capture released in full gets no entry. Counts are the profile's shares
times the column's non-null rows (the profile keeps shares, rounded to its own precision).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from shape.privacy.cells import OTHER_BUCKET, non_null_base

from .errors import VaultInputError
from .format import encode_value, seal_vault
from .kek import resolve_kek
from .ops import ensure_git_ignored, reference_for, write_file
from .policy import VaultPolicy, load_policy, parse_policy

_CATEGORY_SURFACES = ("enum_values", "value_counts_ext")
_EXTREME_SURFACES = ("min_value", "max_value")


def _require_crypto() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "the value vault needs the 'cryptography' package: pip install 'sqllocks-shape[sign]'"
        ) from e


def coerce_policy(policy: Any) -> VaultPolicy:
    """A :class:`VaultPolicy` from a policy object, a decoded policy document or a policy file."""
    if isinstance(policy, VaultPolicy):
        return policy
    if isinstance(policy, Mapping):
        return parse_policy(dict(policy))
    if isinstance(policy, (str, os.PathLike)):
        return load_policy(policy)
    raise VaultInputError("vault_policy must be a VaultPolicy, a policy document or a file path")


def coerce_kek(kek: Any) -> bytes:
    """The 32 key bytes: given as bytes, or named by a credential reference."""
    if isinstance(kek, (bytes, bytearray)):
        if len(kek) != 32:
            raise VaultInputError("the key-encryption key must be 32 bytes")
        return bytes(kek)
    if isinstance(kek, str):
        return resolve_kek(kek)
    raise VaultInputError("kek must be 32 bytes or a credential reference (env://, file://)")


def kek_file_path(kek: Any) -> str | None:
    """The file a ``kek`` reference reads, when it names one (for the git guard)."""
    if not isinstance(kek, str) or kek.startswith(("env://", "kv://")):
        return None
    return kek[len("file://") :] if kek.startswith("file://") else kek if "://" not in kek else None


def _tables(data: Mapping[str, Any]) -> dict[str, Any]:
    return dict(data["tables"]) if "tables" in data else {str(data["name"]): data}


def _classification_map(
    tables: Mapping[str, Any], classifications: Mapping[str, str] | None
) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for key, label in (classifications or {}).items():
        exact = [(t, key.removeprefix(f"{t}.")) for t in tables if key.startswith(f"{t}.")]
        found = [(t, c) for t, c in exact if c in tables[t]["columns"]] or [
            (t, key) for t in tables if key in tables[t]["columns"]
        ]
        for target in found:
            out[target] = label
    return out


def _categories(col: Mapping[str, Any], surface: str, base: int) -> list[list[Any]] | None:
    shares = col.get(surface)
    if not isinstance(shares, Mapping) or not shares:
        return None
    pairs = [
        (str(value), int(round(float(share) * base)))
        for value, share in shares.items()
        if value != OTHER_BUCKET
    ]
    pairs.sort(key=lambda r: (-r[1], r[0]))
    return [[value, count] for value, count in pairs]


def _tagged(tag: Any) -> list[Any] | None:
    if isinstance(tag, list) and len(tag) == 2 and isinstance(tag[0], str):
        return [tag[0], encode_value(tag[1])]
    return None


def _withheld_categories(safe_col: Mapping[str, Any], entry: Mapping[str, Any]) -> bool:
    suppressed = entry.get("suppressed") or {}
    enum = safe_col.get("enum_values")
    return (
        any(s in suppressed for s in _CATEGORY_SURFACES)
        or int(entry.get("categories_dropped") or 0) > 0
        or (isinstance(enum, Mapping) and OTHER_BUCKET in enum)
    )


def _withheld_extremes(entry: Mapping[str, Any]) -> bool:
    suppressed = entry.get("suppressed") or {}
    return any(s in suppressed for s in _EXTREME_SURFACES)


def vault_columns(
    full: Any,
    safe: Any,
    policy: VaultPolicy,
    classifications: Mapping[str, str] | None = None,
) -> tuple[dict[str, tuple[str, dict[str, Any]]], list[dict[str, str]]]:
    """``({"TABLE.COLUMN": (policy, payload)}, skipped)`` for the full profile ``full`` and its
    safe capture ``safe``. ``skipped`` lists the columns whose policy asked for something the safe
    capture did not withhold (nothing to vault)."""
    full_tables = _tables(full.to_dict())
    safe_tables = _tables(safe.to_dict())
    manifest = safe.redaction_manifest.get("tables", {})
    names = {f"{t}.{c}" for t, table in full_tables.items() for c in table["columns"]}
    policy.validate_against(names)
    classified = _classification_map(full_tables, classifications)
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    skipped: list[dict[str, str]] = []
    for tname, table in full_tables.items():
        row_count = int(table.get("row_count") or 0)
        for cname, col in table["columns"].items():
            column = f"{tname}.{cname}"
            chosen = policy.policy_for(column, classified.get((tname, cname)))
            if chosen == "none":
                continue
            entry = manifest.get(tname, {}).get(cname, {})
            safe_col = safe_tables[tname]["columns"][cname]
            payload: dict[str, Any] = {}
            if chosen in ("categories", "all") and _withheld_categories(safe_col, entry):
                base = non_null_base(row_count, col.get("null_count"), col.get("null_rate") or 0.0)
                payload["dtype"] = str(col.get("dtype"))
                payload["rows"] = int(base)
                for surface in _CATEGORY_SURFACES:
                    rows = _categories(col, surface, int(base))
                    if rows is not None:
                        payload[surface] = rows
            if chosen in ("extremes", "all") and _withheld_extremes(entry):
                payload.setdefault("dtype", str(col.get("dtype")))
                for key, surface in (("min", "min_value"), ("max", "max_value")):
                    tagged = _tagged(col.get(surface))
                    if tagged is not None and surface in (entry.get("suppressed") or {}):
                        payload[key] = tagged
            if len(payload) <= 1:  # nothing but the type: the safe capture withheld nothing asked
                skipped.append({"column": column, "policy": chosen, "reason": "nothing withheld"})
                continue
            out[column] = (chosen, payload)
    return out, skipped


def write_profile_vault(
    full: Any,
    safe: Any,
    content_id: str,
    vault_path: str | os.PathLike[str],
    policy: Any,
    kek: Any,
    classifications: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Seal and write the vault of ``full`` (the in-memory profile) for the profile whose
    content id is ``content_id``; returns ``{"reference", "columns", "skipped"}``. Everything is
    checked (policy against the profile, the key, git) before a byte is written."""
    _require_crypto()
    pol = coerce_policy(policy)
    key = coerce_kek(kek)
    columns, skipped = vault_columns(full, safe, pol, classifications)
    guarded = [(vault_path, "vault")]
    key_file = kek_file_path(kek)
    if key_file is not None:
        guarded.append((key_file, "key-encryption key file"))
    ensure_git_ignored(guarded)
    raw = seal_vault(columns, content_id, key)
    write_file(Path(vault_path), raw)
    return {"reference": reference_for(raw), "columns": sorted(columns), "skipped": skipped}
