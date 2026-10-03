"""A structural, fail-closed leak scanner for a serialized safe profile.

It reads the artifact file only, never the data, so it works as a pre-commit or CI gate: the
exit code is 0 only for an artifact proven clean.

The rules deny by shape, not by name, so a renamed field does not slip through:

* every dict and list is walked, whatever its key, so legacy or list-shaped columns are
  scanned too;
* a list of more than ``max_raw_string_list`` raw strings is a value dump;
* a numeric extreme pair (``min``/``max`` and spellings of them) under any parent is a leak,
  except under the closed allowlist of length aggregates, ``string_length`` and
  ``length_dist``, which are computed from ``len()`` and never carry a value;
* any string matching a personal-data pattern (SSN, email, phone, IPv4, IBAN) anywhere is
  a leak.

It fails closed on ambiguity: a table without a usable ``row_count``, or an artifact without
the safe-profile markers (``schema_version`` or ``redaction_manifest``), is flagged and still
walked, so the report names the concrete leaks. An artifact stamped ``unsafe`` is rejected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["SafeProfileValidator", "ValidationFinding", "ValidationResult"]

MAX_RAW_STRING_LIST = 2
SAFE_MINMAX_CONTAINERS = frozenset({"string_length", "length_dist"})
_MIN_KEYS = ("min", "min_value", "minimum")
_MAX_KEYS = ("max", "max_value", "maximum")
# A safe profile is a shallow document. Anything nested deeper is rejected before it is walked, so a
# hostile artifact cannot exhaust the interpreter stack (RecursionError) in the recursive scan.
MAX_NESTING_DEPTH = 64


def _nesting_depth(data: Any, limit: int) -> int:
    """Depth of ``data`` (iterative, stops once it exceeds ``limit``)."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        if deepest > limit:
            return deepest
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return deepest


SAFE_SCHEMA_MARKERS = frozenset({"schema_version", "redaction_manifest"})

# Unanchored on purpose: personal data embedded anywhere inside a value must be found.
_PII_REGEXES: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # The lookbehind starts a match only at the start of a local-part run. Without it the search
    # restarts at every character of a long run with no "@" and takes quadratic time (a 70 KB
    # string cost over 5 s: a denial of service on an untrusted artifact, P7-04).
    "email": re.compile(r"(?<![a-zA-Z0-9._%+\-])[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    "ip": re.compile(
        r"\b(?:25[0-5]|2[0-4]\d|[01]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d?\d)){3}\b"
    ),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\d\-\.\s\(\)]{6,18}\d)(?!\d)"),
}
_PHONE_MIN_DIGITS = 7
_PHONE_MAX_DIGITS = 15


@dataclass
class ValidationFinding:
    """One leak finding and the JSON path that triggered it."""

    rule: str
    path: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "path": self.path, "detail": self.detail}


@dataclass
class ValidationResult:
    """The outcome of a scan: clean only when there are no findings."""

    path: str
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.findings

    @property
    def exit_code(self) -> int:
        return 0 if self.is_clean else 1

    def add(self, rule: str, path: str, detail: str) -> None:
        self.findings.append(ValidationFinding(rule, path, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "clean": self.is_clean,
            "exit_code": self.exit_code,
            "findings": [f.to_dict() for f in self.findings],
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


class SafeProfileValidator:
    """Scan a serialized safe profile for value leaks.

    ``SafeProfileValidator().validate_file("profile.json").exit_code`` is 0 only for a clean
    artifact.
    """

    def __init__(self, max_raw_string_list: int = MAX_RAW_STRING_LIST) -> None:
        self.max_raw_string_list = max_raw_string_list

    def validate_file(self, path: str | Path) -> ValidationResult:
        """Load and scan a JSON artifact. Any read or parse error is a finding."""
        path = Path(path)
        result = ValidationResult(path=str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.add("unreadable", str(path), f"cannot read artifact: {exc}")
            return result
        try:
            data = json.loads(text)
        except (ValueError, RecursionError) as exc:
            result.add("malformed", str(path), f"not valid JSON: {exc}")
            return result
        return self.validate_data(data, path=str(path))

    def validate_data(self, data: Any, path: str = "<data>") -> ValidationResult:
        """Scan an already parsed artifact."""
        result = ValidationResult(path=path)
        if _nesting_depth(data, MAX_NESTING_DEPTH) > MAX_NESTING_DEPTH:
            result.add(
                "malformed",
                "$",
                f"nesting deeper than {MAX_NESTING_DEPTH} levels; a safe profile is shallow",
            )
            return result
        if not (isinstance(data, dict) and SAFE_SCHEMA_MARKERS & data.keys()):
            result.add(
                "not-safe-profile",
                "$",
                f"artifact lacks safe-profile markers ({sorted(SAFE_SCHEMA_MARKERS)}); "
                "foreign or full-fidelity JSON is flagged, only proven-clean artifacts pass",
            )
        if isinstance(data, dict) and data.get("unsafe", False) is not False:
            result.add(
                "unsafe-stamp",
                "$.unsafe",
                "artifact is stamped unsafe (the full-fidelity opt-out); it is rejected",
            )
        if isinstance(data, dict):
            self._check_format_version(data, result)
        self._check_row_counts(data, result)
        self._walk(data, "$", None, result)
        return result

    @staticmethod
    def _check_format_version(data: dict[str, Any], result: ValidationResult) -> None:
        """The scanner knows the value-bearing fields of the versions this release reads only: a
        newer version (it may add fields), a malformed version or another format is not proven
        clean."""
        from shape import compat

        try:
            compat.check_format("safe-profile", data)
            compat.check_readable("safe-profile", data)
        except compat.UnsupportedVersionError as exc:
            result.add("unsupported-version", "$.version", str(exc))
        except compat.FormatError as exc:
            result.add("format-version", "$", str(exc))

    def _check_row_counts(self, data: Any, result: ValidationResult) -> None:
        """Every table must carry a positive integer ``row_count``, or its safety is unknown."""
        tables = data.get("tables") if isinstance(data, dict) else None
        if not isinstance(tables, dict):
            return
        for tname, tnode in tables.items():
            if not isinstance(tnode, dict):
                result.add(
                    "row-count-missing",
                    f"$.tables.{tname}",
                    "table node is not an object; row_count undeterminable",
                )
                continue
            rc = tnode.get("row_count")
            if not isinstance(rc, int) or isinstance(rc, bool) or rc <= 0:
                result.add(
                    "row-count-missing",
                    f"$.tables.{tname}.row_count",
                    f"row_count absent, unknown or non-positive ({rc!r}); node safety "
                    "undeterminable, flagged",
                )

    def _walk(self, node: Any, path: str, parent_key: str | None, result: ValidationResult) -> None:
        if isinstance(node, dict):
            self._check_extreme_pair(node, path, parent_key, result)
            for key, value in node.items():
                child = f"{path}.{key}"
                if isinstance(value, str):
                    self._check_pii(value, child, result)
                self._walk(value, child, key, result)
        elif isinstance(node, list):
            self._check_raw_string_list(node, path, parent_key, result)
            for idx, item in enumerate(node):
                child = f"{path}[{idx}]"
                if isinstance(item, str):
                    self._check_pii(item, child, result)
                self._walk(item, child, parent_key, result)
        elif isinstance(node, str):
            self._check_pii(node, path, result)

    def _check_extreme_pair(
        self, node: dict[str, Any], path: str, parent_key: str | None, result: ValidationResult
    ) -> None:
        min_key = next((k for k in _MIN_KEYS if k in node and _is_number(node[k])), None)
        max_key = next((k for k in _MAX_KEYS if k in node and _is_number(node[k])), None)
        if min_key is None or max_key is None or parent_key in SAFE_MINMAX_CONTAINERS:
            return
        result.add(
            "extreme-pair",
            path,
            f"numeric extreme pair ({min_key}={node[min_key]!r}, {max_key}={node[max_key]!r}) "
            f"under {parent_key!r}: a raw minimum and maximum can identify a record. "
            f"Exempt only under {sorted(SAFE_MINMAX_CONTAINERS)}.",
        )

    def _check_raw_string_list(
        self, node: list[Any], path: str, parent_key: str | None, result: ValidationResult
    ) -> None:
        strings = [item for item in node if isinstance(item, str)]
        if len(strings) > self.max_raw_string_list:
            result.add(
                "raw-string-list",
                path,
                f"list under {parent_key!r} carries {len(strings)} raw strings "
                f"(more than {self.max_raw_string_list}): a value dump. Sample: {strings[:3]!r}",
            )

    def _check_pii(self, value: str, path: str, result: ValidationResult) -> None:
        for label, rx in _PII_REGEXES.items():
            m = rx.search(value)
            if not m:
                continue
            if label == "phone":
                digits = sum(c.isdigit() for c in m.group(0))
                if not _PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS:
                    continue
            result.add("pii-regex", path, f"value matches the {label} pattern: {m.group(0)!r}")
            return
