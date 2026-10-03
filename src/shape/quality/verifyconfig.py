"""The configuration document of the gates that the schema does not drive.

``shape verify --config FILE.json`` reads a document of Shape's own (``format:
shape-verify-config``) and runs ``range_constraint``, ``temporal_consistency``,
``schema_drift`` and ``file_format`` next to the schema gates, with the same settings that
``ValidationContext.config`` takes from Python. A key that Shape does not know is an error, so
a misspelt rule can never be skipped without notice.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shape import compat

FORMAT = "shape-verify-config"
VERSION = compat.KINDS["verify-config"].current

_KEYS = (
    "ranges",
    "date_range",
    "no_future",
    "ordering",
    "baseline",
    "distribution_alpha",
    "file_paths",
    "check_data_files",
)


class VerifyConfigError(ValueError):
    """A document is not a usable verify configuration."""


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and value.count(".") >= 1 and all(value.split(".", 1))


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_ranges(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise VerifyConfigError('"ranges" must be an object of "table.column": {min, max}')
    for key, bounds in value.items():
        if not _is_name(key):
            raise VerifyConfigError(f'ranges: key "{key}" must be "table.column"')
        if not isinstance(bounds, Mapping) or not bounds:
            raise VerifyConfigError(f'ranges["{key}"] must be an object with "min" and/or "max"')
        for b, v in bounds.items():
            if b not in ("min", "max"):
                raise VerifyConfigError(f'ranges["{key}"]: unknown key "{b}" (use "min", "max")')
            if not _number(v):
                raise VerifyConfigError(f'ranges["{key}"]["{b}"] must be a number, not {v!r}')


def _check_date(where: str, value: Any) -> None:
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise VerifyConfigError(f"{where}: {value!r} is not an ISO 8601 date or time") from exc
    else:
        raise VerifyConfigError(f"{where} must be an ISO 8601 string, not {value!r}")


def _check_date_range(value: Any) -> None:
    if not isinstance(value, Mapping) or not value:
        raise VerifyConfigError('"date_range" must be an object with "start" and/or "end"')
    for k, v in value.items():
        if k not in ("start", "end"):
            raise VerifyConfigError(f'date_range: unknown key "{k}" (use "start", "end")')
        _check_date(f'date_range["{k}"]', v)


def _check_no_future(value: Any) -> None:
    if not isinstance(value, list) or not all(_is_name(v) for v in value):
        raise VerifyConfigError('"no_future" must be a list of "table.column" names')


def _check_ordering(value: Any) -> None:
    if not isinstance(value, list):
        raise VerifyConfigError('"ordering" must be a list of {"table", "start", "end"} rules')
    for i, rule in enumerate(value):
        if not isinstance(rule, Mapping):
            raise VerifyConfigError(f"ordering[{i}] must be an object")
        for key in ("table", "start", "end"):
            if not isinstance(rule.get(key), str) or not rule[key]:
                raise VerifyConfigError(f'ordering[{i}]: missing required key "{key}"')
        extra = sorted(set(rule) - {"table", "start", "end"})
        if extra:
            raise VerifyConfigError(f"ordering[{i}]: unknown key {extra[0]!r}")


def _check_baseline(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise VerifyConfigError('"baseline" must be {"table": {"columns": {"col": "dtype"}}}')
    for tname, t in value.items():
        cols = t.get("columns") if isinstance(t, Mapping) else None
        if not isinstance(cols, Mapping) or not all(isinstance(v, str) for v in cols.values()):
            raise VerifyConfigError(
                f'baseline["{tname}"] must be {{"columns": {{"col": "dtype"}}}} '
                '(dtypes are names such as "int64", "float64", "str")'
            )


def _check_alpha(value: Any) -> None:
    if not _number(value) or not 0 < value < 1:
        raise VerifyConfigError('"distribution_alpha" must be a number between 0 and 1')


def _check_file_paths(value: Any) -> None:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise VerifyConfigError('"file_paths" must be a list of file paths')


def _check_bool(value: Any) -> None:
    if not isinstance(value, bool):
        raise VerifyConfigError('"check_data_files" must be true or false')


_CHECKS = {
    "ranges": _check_ranges,
    "date_range": _check_date_range,
    "no_future": _check_no_future,
    "ordering": _check_ordering,
    "baseline": _check_baseline,
    "distribution_alpha": _check_alpha,
    "file_paths": _check_file_paths,
    "check_data_files": _check_bool,
}


@dataclass(frozen=True, slots=True)
class VerifyConfig:
    """The settings of the four config-driven gates.

    ``rules`` holds the ``ValidationContext.config`` keys (``ranges``, ``date_range``,
    ``no_future``, ``ordering``, ``baseline``, ``distribution_alpha``); ``file_paths`` are the
    files the ``file_format`` gate reads, and ``check_data_files`` adds every data file that
    ``shape verify`` loaded."""

    rules: Mapping[str, Any] = field(default_factory=dict)
    file_paths: tuple[str, ...] = ()
    check_data_files: bool = False

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> VerifyConfig:
        if not isinstance(doc, Mapping):
            raise VerifyConfigError("a verify configuration must be a JSON object")
        if doc.get("format") != FORMAT:
            raise VerifyConfigError(f"not a verify configuration: expected format {FORMAT!r}")
        compat.check_readable("verify-config", doc, error=VerifyConfigError)
        unknown = sorted(
            k
            for k in doc
            if k not in {*compat.BOOKKEEPING_KEYS, *_KEYS} and not str(k).startswith("x_")
        )
        if unknown:
            raise VerifyConfigError(
                f"unknown key {unknown[0]!r} in the verify configuration "
                f"(known: {', '.join(_KEYS)})"
            )
        for key in _KEYS:
            if key in doc:
                _CHECKS[key](doc[key])
        rules = {k: doc[k] for k in _KEYS[:6] if k in doc}
        return cls(
            rules,
            tuple(doc.get("file_paths") or ()),
            bool(doc.get("check_data_files", False)),
        )

    @property
    def temporal(self) -> bool:
        return any(k in self.rules for k in ("date_range", "no_future", "ordering"))


def load_verify_config(path: str | Path) -> VerifyConfig:
    """A verify configuration from a JSON file."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise VerifyConfigError(f"{p} is not valid JSON: {exc}") from exc
    return VerifyConfig.from_dict(doc)
