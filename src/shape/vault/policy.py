"""The vault policy file: which columns keep which withheld values in the vault.

::

    {"format": "shape-vault-policy", "version": 1, "default": "none",
     "by_classification": {"CONFIDENTIAL": "categories"},
     "columns": {"TABLE.COLUMN": "categories" | "extremes" | "all" | "none"}}

An explicit ``columns`` entry wins over ``by_classification`` (the ordered taxonomy of
``docs/PRIVACY_MODEL.md``; an alias such as ``PII`` stands for its level), which wins over
``default``. A classification entry applies to the columns of exactly that level.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape import compat
from shape.privacy.classification import ALIASES, DEFAULT_LEVELS

from .errors import VaultInputError

POLICY_VALUES = ("categories", "extremes", "all", "none")
_KNOWN = ("default", "by_classification", "columns")
MAX_POLICY_BYTES = 8 * 1024 * 1024


def _canonical_level(label: str) -> str | None:
    name = label.strip().upper()
    name = ALIASES.get(name, name)
    return name if name in DEFAULT_LEVELS else None


def _policy_value(value: Any, where: str) -> str:
    if not isinstance(value, str) or value not in POLICY_VALUES:
        shown = value if isinstance(value, str) and len(value) <= 40 else "a non-text value"
        raise VaultInputError(
            f"{where}: unknown policy {shown!r} (use one of {', '.join(POLICY_VALUES)})"
        )
    return value


@dataclass(frozen=True)
class VaultPolicy:
    default: str = "none"
    by_classification: Mapping[str, str] = field(default_factory=dict)
    columns: Mapping[str, str] = field(default_factory=dict)

    def policy_for(self, column: str, classification: str | None = None) -> str:
        """The policy of ``TABLE.COLUMN``: explicit entry, else its classification's, else the
        default."""
        if column in self.columns:
            return self.columns[column]
        if classification is not None:
            level = _canonical_level(classification)
            if level is not None and level in self.by_classification:
                return self.by_classification[level]
        return self.default

    def validate_against(self, profile_columns: Collection[str]) -> None:
        """A column that is not in the profile is an input error naming it."""
        known = set(profile_columns)
        for name in sorted(self.columns):
            if name not in known:
                raise VaultInputError(f"policy column {name!r} is not in the profile")

    def to_dict(self) -> dict[str, Any]:
        return compat.stamp(
            "vault-policy",
            {
                "default": self.default,
                "by_classification": dict(self.by_classification),
                "columns": dict(self.columns),
            },
            aliases=False,
        )


def parse_policy(doc: Any) -> VaultPolicy:
    """A :class:`VaultPolicy` from a decoded policy file; anything unusable is a
    :class:`VaultInputError` that names the entry."""
    if not isinstance(doc, dict):
        raise VaultInputError("the vault policy must be a JSON object")
    compat.check_format("vault-policy", doc, error=VaultInputError)
    if compat.FORMAT_KEY not in doc:
        raise VaultInputError("the vault policy declares no format (shape-vault-policy)")
    compat.check_readable("vault-policy", doc, error=VaultInputError)
    compat.check_unknown("vault-policy", doc, _KNOWN, error=VaultInputError)
    default = _policy_value(doc.get("default", "none"), "default")
    by_class: dict[str, str] = {}
    raw_class = doc.get("by_classification", {})
    if not isinstance(raw_class, dict):
        raise VaultInputError("by_classification must be an object")
    for label, value in raw_class.items():
        level = _canonical_level(label) if isinstance(label, str) else None
        if level is None:
            shown = label if isinstance(label, str) and len(label) <= 40 else "?"
            raise VaultInputError(f"by_classification: unknown classification {shown!r}")
        by_class[level] = _policy_value(value, f"by_classification.{label}")
    columns: dict[str, str] = {}
    raw_cols = doc.get("columns", {})
    if not isinstance(raw_cols, dict):
        raise VaultInputError("columns must be an object")
    for name, value in raw_cols.items():
        if not isinstance(name, str) or "." not in name:
            raise VaultInputError("columns: names are written TABLE.COLUMN")
        columns[name] = _policy_value(value, f"columns.{name}")
    return VaultPolicy(default, by_class, columns)


def load_policy(path: str | Path) -> VaultPolicy:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_POLICY_BYTES:
            raise VaultInputError(f"{p} is too large to be a vault policy")
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        raise VaultInputError(f"{p} is not a JSON vault policy") from None
    return parse_policy(doc)
