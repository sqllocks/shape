"""The planned-change file (``shape-changes.yml``, W1-12): changes that are expected and reviewed.

A planned change inside its window is reported as planned and does not fail ``shape diff``,
``shape check`` or ``shape verify``; a suppressed one is not reported; an expired entry stops
matching. The JSON Schema in ``shape/schemas/shape-planned-changes-v1.schema.json`` is the
structural truth (checked with :mod:`shape.schemacheck`); what a schema cannot say (id syntax,
real calendar dates, kinds, ``until`` not before ``from``, unique ids) is checked here. Every
problem is reported at once, each with the entry id and the key.

Writes (``shape changes add`` and ``ack``) append text to the file, so comments, key order and
every entry that was already there stay byte for byte as they were.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.schemacheck import validate

FORMAT = "shape-planned-changes"
VERSION = 1
DEFAULT_NAME = "shape-changes.yml"
MAX_BYTES = 1 << 20

ACTIONS = ("expect", "suppress", "severity")
SEVERITIES = ("low", "medium", "high")
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_KEYS = ("from", "until", "acknowledged_at")
_KEY_ORDER = (
    "id",
    "source",
    "column",
    "kinds",
    "from",
    "until",
    "action",
    "severity",
    "class",
    "reason",
    "owner",
    "ticket",
    "acknowledged_by",
    "acknowledged_at",
)

#: Contract rule names (``shape check``) that can be planned, next to the drift kinds. A
#: violation of the ``nullable`` rule also matches the name ``not_null``.
CONTRACT_RULES = frozenset(
    {
        "dtype",
        "nullable",
        "not_null",
        "unique",
        "max_null_rate",
        "pattern",
        "allowed_values",
        "min",
        "max",
        "distribution",
        "min_true_rate",
        "max_true_rate",
        "no_placeholder",
        "column_exists",
        "required_column",
        "extra_column",
        "table_exists",
        "fd",
        "implies",
        "reference_pair",
        "max_implausible_rate",
    }
)
_RULE_ALIASES = {"nullable": "not_null"}


class ChangesError(ShapeError, ValueError):
    """The planned-change file cannot be read or is not valid. ``problems`` holds every problem
    found (without the file name)."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.problems = problems or (message,)


class ChangesVersionError(ChangesError):
    """The file was written for a newer version of the format than this Shape understands."""


@cache
def schema() -> dict[str, Any]:
    """The JSON Schema of the planned-change file, as shipped in ``shape/schemas``."""
    text = (
        resources.files("shape")
        .joinpath("schemas/shape-planned-changes-v1.schema.json")
        .read_text("utf-8")
    )
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def known_kinds() -> frozenset[str]:
    """Every name ``kinds`` accepts: the drift kinds of ``docs/DRIFT.md`` and the contract rules."""
    from shape.drift.engine import KIND_SEVERITY

    return frozenset(KIND_SEVERITY) | CONTRACT_RULES


def parse_day(value: Any, what: str = "date") -> date:
    """A calendar date from ``YYYY-MM-DD`` text (or a date). Raises ``ValueError``."""
    if isinstance(value, datetime):
        raise ValueError(f"{what}: {value!r} is not a date (use YYYY-MM-DD)")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{what}: invalid date {value!r} (use YYYY-MM-DD)")


def today() -> date:
    return datetime.now(UTC).date()


# ---- the model ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannedChange:
    id: str
    column: str
    kinds: tuple[str, ...]
    until: date
    reason: str
    source: str | None = None
    from_: date | None = None
    action: str = "expect"
    severity: str | None = None
    owner: str | None = None
    ticket: str | None = None
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    class_: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The entry as a plain mapping (dates as ISO text), in the file's key order."""
        raw: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "column": self.column,
            "kinds": list(self.kinds),
            "from": self.from_.isoformat() if self.from_ else None,
            "until": self.until.isoformat(),
            "action": self.action,
            "severity": self.severity,
            "class": self.class_,
            "reason": self.reason,
            "owner": self.owner,
            "ticket": self.ticket,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
        }
        return {k: raw[k] for k in _KEY_ORDER if raw[k] is not None}

    def active_on(self, day: date) -> bool:
        return (self.from_ is None or self.from_ <= day) and day <= self.until

    def applies_to(self, source: str | None) -> bool:
        return self.source is None or self.source == source

    def covers_kind(self, kind: str) -> bool:
        return kind in self.kinds or _RULE_ALIASES.get(kind) in self.kinds

    def covers_column(self, table: str | None, column: str | None) -> bool:
        if self.column == "*":
            return True
        if column is None:
            names = [table] if table else []
        else:
            names = [column, *([f"{table}.{column}"] if table else [])]
        return any(fnmatch.fnmatchcase(n, self.column) for n in names)


def _entry(raw: Mapping[str, Any]) -> PlannedChange:
    return PlannedChange(
        id=raw["id"],
        column=raw["column"],
        kinds=tuple(raw["kinds"]),
        until=date.fromisoformat(raw["until"]),
        reason=raw["reason"],
        source=raw.get("source"),
        from_=date.fromisoformat(raw["from"]) if "from" in raw else None,
        action=raw.get("action", "expect"),
        severity=raw.get("severity"),
        owner=raw.get("owner"),
        ticket=raw.get("ticket"),
        acknowledged_by=raw.get("acknowledged_by"),
        acknowledged_at=raw.get("acknowledged_at"),
        class_=raw.get("class"),
    )


class Applier:
    """Matches changes against the entries active on one day and remembers what matched."""

    def __init__(self, entries: Sequence[PlannedChange], on: date, source: str | None) -> None:
        self.on = on
        self.source = source
        self._entries = [e for e in entries if e.applies_to(source)]
        self._hits: dict[str, int] = {}
        self._expired: dict[str, PlannedChange] = {}

    def match(self, table: str | None, column: str | None, kind: str) -> PlannedChange | None:
        """The first active entry (file order) that covers the change, or None. An entry past its
        ``until`` that would have matched is remembered as expired."""
        for e in self._entries:
            if not (e.covers_kind(kind) and e.covers_column(table, column)):
                continue
            if self.on > e.until:
                self._expired.setdefault(e.id, e)
            elif e.active_on(self.on):
                self._hits[e.id] = self._hits.get(e.id, 0) + 1
                return e
        return None

    def report(self) -> dict[str, list[dict[str, Any]]]:
        """``planned`` (entries that matched, with ``matches``), ``planned_not_observed`` (active
        ``expect`` entries that matched nothing) and ``expired``."""
        planned = [
            {**e.to_dict(), "matches": self._hits[e.id]}
            for e in self._entries
            if e.id in self._hits
        ]
        unseen = [
            e.to_dict()
            for e in self._entries
            if e.action == "expect" and e.id not in self._hits and e.active_on(self.on)
        ]
        expired = [e.to_dict() for e in self._entries if e.id in self._expired]
        return {"planned": planned, "planned_not_observed": unseen, "expired": expired}

    def expired_notices(self) -> list[str]:
        return [
            f"planned change {e.id} expired on {e.until.isoformat()}"
            for e in self._entries
            if e.id in self._expired
        ]


def apply_to_violations(
    applier: Applier, violations: Sequence[Mapping[str, Any]], table: str | None
) -> tuple[list[dict[str, Any]], list[bool]]:
    """``shape check``: the contract violations with the planned ones marked or left out, and
    for each kept one whether it still fails. ``expect`` does not fail, ``suppress`` is not
    reported, ``severity`` is reported with its severity and fails only at ``high``."""
    kept: list[dict[str, Any]] = []
    counted: list[bool] = []
    for v in violations:
        hit = applier.match(table, v.get("column"), v["rule"])
        if hit is None:
            kept.append(dict(v))
            counted.append(True)
        elif hit.action == "suppress":
            continue
        elif hit.action == "severity":
            kept.append(
                {**v, "severity": hit.severity, "planned": {"id": hit.id, "action": hit.action}}
            )
            counted.append(hit.severity == "high")
        else:
            kept.append({**v, "planned": {"id": hit.id, "action": hit.action}})
            counted.append(False)
    return kept, counted


@dataclass(frozen=True, slots=True)
class PlannedChanges:
    path: Path | None
    document: Mapping[str, Any]
    entries: tuple[PlannedChange, ...] = field(default_factory=tuple)

    @property
    def version(self) -> int:
        return int(self.document["version"])

    def applier(self, on: date | str | None = None, source: str | None = None) -> Applier:
        day = today() if on is None else parse_day(on, "on")
        return Applier(self.entries, day, source)

    def active_on(self, day: date) -> list[PlannedChange]:
        return [e for e in self.entries if e.active_on(day)]


# ---- parsing and validation --------------------------------------------------------------------


def _normalise_dates(doc: Any) -> None:
    """YAML reads ``2026-11-02`` as a date: the model keeps ISO text, as JSON does."""
    items = doc.get("changes") if isinstance(doc, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        for key in _DATE_KEYS:
            v = item.get(key)
            if isinstance(v, datetime):
                item[key] = v.isoformat() if key == "acknowledged_at" else v
            elif isinstance(v, date):
                item[key] = v.isoformat()


def _label(doc: dict[str, Any], index: int) -> str:
    items = doc.get("changes")
    item = items[index] if isinstance(items, list) and index < len(items) else None
    ident = item.get("id") if isinstance(item, dict) else None
    return ident if isinstance(ident, str) and ident else f"changes[{index}]"


def _newer(version: int) -> str:
    return (
        f"version {version} is newer than this Shape understands (it reads up to version "
        f"{VERSION}): upgrade Shape, or lower the file's version if it uses nothing newer"
    )


def _entry_problems(item: dict[str, Any], label: str, out: list[str]) -> None:
    def bad(key: str, message: str) -> None:
        out.append(f"{label}: {key}: {message}")

    if isinstance(item.get("id"), str) and not _ID.fullmatch(item["id"]):
        bad(
            "id",
            f"{item['id']!r} is not valid (lowercase letters, digits, . _ -; "
            "it starts with a letter or digit)",
        )
    for key in ("source", "column", "reason", "owner", "ticket", "acknowledged_by"):
        if isinstance(item.get(key), str) and not item[key].strip():
            bad(key, "must not be empty")
    kinds = item.get("kinds")
    if isinstance(kinds, list):
        if not kinds:
            bad("kinds", "needs at least one kind")
        known = known_kinds()
        for i, k in enumerate(kinds):
            if isinstance(k, str) and k not in known:
                bad(
                    f"kinds[{i}]",
                    f"unknown kind {k!r} (drift kinds are listed in docs/DRIFT.md; "
                    "the contract rules are those of `shape check`)",
                )
    days: dict[str, date] = {}
    for key in ("from", "until"):
        v = item.get(key)
        if isinstance(v, str):
            try:
                days[key] = parse_day(v, key)
            except ValueError:
                bad(key, f"{v!r} is not a calendar date (use YYYY-MM-DD)")
    if "from" in days and "until" in days and days["until"] < days["from"]:
        bad("until", f"{item['until']} is before from ({item['from']})")
    action = item.get("action", "expect")
    if action == "severity" and "severity" not in item:
        bad("severity", "required when action is severity (low, medium or high)")
    if "severity" in item and action != "severity":
        bad("severity", "only applies to action severity")


def problems(doc: Any) -> list[str]:
    """Every problem of a parsed document (empty when valid), each naming the entry id and the
    key. A document of a newer version is reported as that and nothing else."""
    if not isinstance(doc, dict):
        return [f"document: must be a mapping (key: value pairs), got {type(doc).__name__}"]
    if doc.get("format") == FORMAT:
        version = doc.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version > VERSION:
            return [_newer(version)]
    _normalise_dates(doc)
    found: list[str] = []
    for line in validate(doc, schema()):
        path, _, message = line.partition(": ")
        m = re.match(r"^\$\.changes\[(\d+)\](?:\.(.*))?$", path)
        if m:
            label = _label(doc, int(m[1]))
            where = m[2] or "entry"
            message = re.sub(r"^unexpected key '(.*)'$", r"unknown key '\1'", message)
            found.append(f"{label}: {where}: {message}")
        else:
            found.append(f"{path.removeprefix('$.') if path != '$' else 'document'}: {message}")
    items = doc.get("changes")
    seen: dict[str, int] = {}
    for i, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        label = _label(doc, i)
        _entry_problems(item, label, found)
        ident = item.get("id")
        if isinstance(ident, str):
            if ident in seen:
                found.append(f"{label}: id: duplicate id (also entry {seen[ident]})")
            else:
                seen[ident] = i
        for key in ("from", "until"):
            if key in item and not isinstance(item[key], str):
                pass  # the schema reported the type
    return found


def _read_text(text: str, json_file: bool) -> Any:
    if json_file:
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ChangesError(f"invalid JSON: {exc}") from None
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise ChangesError(
            'reading shape-changes.yml needs PyYAML: pip install "sqllocks-shape[yaml]" '
            "(or write the file as JSON)"
        ) from None
    from shape.project.file import ProjectError, _refuse_duplicate_keys
    from shape.security.yamlsafe import MAX_BYTES as YAML_MAX
    from shape.security.yamlsafe import _expanded_size

    class _DatesAsText(yaml.SafeLoader):  # type: ignore[misc]
        """Dates stay text, so `until: 2026-13-45` is a problem of that key, not a YAML error."""

    _DatesAsText.add_constructor(
        "tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node)
    )

    try:
        if len(text) > YAML_MAX:
            raise ValueError(f"YAML document larger than {YAML_MAX} bytes")
        root = yaml.compose(text, Loader=yaml.SafeLoader)  # bounded: no alias bombs
        if root is not None:
            _expanded_size(root, {}, set())
        doc = yaml.load(text, Loader=_DatesAsText)  # noqa: S506 - a SafeLoader subclass
        _refuse_duplicate_keys(text)
        return doc
    except ProjectError as exc:
        raise ChangesError(str(exc)) from None
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        raise ChangesError(f"invalid YAML at {where}{exc.problem}") from None
    except yaml.YAMLError as exc:
        raise ChangesError(f"invalid YAML: {exc}") from None
    except ValueError as exc:
        raise ChangesError(str(exc)) from None


def _from_document(doc: Any, path: Path | None) -> PlannedChanges:
    found = problems(doc)
    if found:
        if isinstance(doc, dict) and isinstance(doc.get("version"), int):
            version = doc["version"]
            if doc.get("format") == FORMAT and not isinstance(version, bool) and version > VERSION:
                raise ChangesVersionError(_newer(version), tuple(found))
        raise ChangesError("; ".join(found), tuple(found))
    return PlannedChanges(path, doc, tuple(_entry(e) for e in doc["changes"]))


def parse_changes(
    text: str, path: str | os.PathLike[str] | None = None, *, json_file: bool | None = None
) -> PlannedChanges:
    """Parse and validate the text of a planned-change file. Raises :class:`ChangesError`
    (:class:`ChangesVersionError` for a newer version); the message starts with the file name."""
    where = Path(path) if path is not None else None
    if json_file is None:
        json_file = where is not None and where.suffix.lower() == ".json"
    prefix = f"{where}: " if where is not None else ""
    try:
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ChangesError("the file is larger than 1 MiB")
        if not text.strip():
            raise ChangesError("the file is empty")
        return _from_document(_read_text(text, json_file), where)
    except ChangesError as exc:
        raise type(exc)(prefix + str(exc), exc.problems) from None


def load_changes(path: str | os.PathLike[str]) -> PlannedChanges:
    """Read a planned-change file. Raises ``FileNotFoundError`` and :class:`ChangesError`."""
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ChangesError(f"{p}: the file is larger than 1 MiB")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ChangesError(f"{p}: not a UTF-8 text file") from None
    return parse_changes(text, p)


def from_entries(entries: Iterable[Mapping[str, Any]]) -> PlannedChanges:
    """Planned changes from a list of entry mappings (``planned=`` of ``shape.diff``)."""
    doc = {"format": FORMAT, "version": VERSION, "changes": [dict(e) for e in entries]}
    try:
        return _from_document(doc, None)
    except ChangesError as exc:
        raise type(exc)("planned changes: " + "; ".join(exc.problems), exc.problems) from None


def coerce(planned: Any) -> PlannedChanges | None:
    """``planned=`` of the API: a :class:`PlannedChanges`, a path, a list of entries, or None."""
    if planned is None or isinstance(planned, PlannedChanges):
        return planned
    if isinstance(planned, (str, os.PathLike)):
        return load_changes(planned)
    if isinstance(planned, Iterable):
        return from_entries(planned)
    raise TypeError("planned must be a PlannedChanges, a path or a list of entries")


# ---- writing (append only) ---------------------------------------------------------------------


def _scalar(value: str) -> str:
    import yaml

    text: str = yaml.safe_dump(value, default_flow_style=True, width=1 << 30).splitlines()[0]
    return text.removesuffix(" ...") if text.endswith(" ...") else text


def entry_text(raw: Mapping[str, Any]) -> str:
    """One entry as block YAML, indented as a ``changes:`` list item."""
    lines: list[str] = []
    for key in _KEY_ORDER:
        if key not in raw or raw[key] is None:
            continue
        v = raw[key]
        if key == "kinds":
            body = "[" + ", ".join(v) + "]"
        elif key in ("from", "until", "acknowledged_at") and isinstance(v, str) and _DATE.match(v):
            body = v
        elif isinstance(v, str):
            body = _scalar(v)
        else:
            body = str(v)
        lines.append(f"{key}: {body}")
    return "\n".join(("  - " if i == 0 else "    ") + line for i, line in enumerate(lines)) + "\n"


_CHANGES_KEY = re.compile(r"^changes:\s*(\[\s*\])?\s*(#.*)?$")


def _append_yaml(text: str, raws: Sequence[Mapping[str, Any]]) -> str:
    lines = text.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(lines) if _CHANGES_KEY.match(ln.rstrip("\r\n"))), None)
    if at is None:
        raise ChangesError("cannot add: no top-level `changes:` list in the file")
    block = "".join(entry_text(r) for r in raws)
    if _CHANGES_KEY.match(lines[at].rstrip("\r\n")) and "[" in lines[at]:
        comment = re.search(r"#.*$", lines[at])
        head = "changes:" + (f" {comment[0]}" if comment else "") + "\n"
        return "".join(lines[:at]) + head + block + "".join(lines[at + 1 :])
    for ln in lines[at + 1 :]:
        if ln.strip() and not ln.startswith((" ", "\t", "#", "-")):
            raise ChangesError(
                "cannot add safely: `changes:` is not the last top-level key of the file "
                "(move it to the end, or edit the file by hand)"
            )
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return "".join(lines) + block


def new_file_text(raws: Sequence[Mapping[str, Any]]) -> str:
    head = (
        "# Planned changes: reviewed, dated exceptions that `shape diff`, `shape check` and\n"
        "# `shape verify` read. See docs/PLANNED_CHANGES.md.\n"
        f"format: {FORMAT}\nversion: {VERSION}\nchanges:\n"
    )
    return head + "".join(entry_text(r) for r in raws)


def add_entries(path: str | os.PathLike[str], raws: Sequence[Mapping[str, Any]]) -> PlannedChanges:
    """Append entries to the file (created when missing). Everything in the file stays as it
    was. The result is validated before anything is written; a duplicate id or an invalid entry
    raises :class:`ChangesError` and writes nothing."""
    p = Path(path)
    raws = [{k: v for k, v in raw.items() if v is not None} for raw in raws]
    for raw in raws:
        for key in ("from", "until", "acknowledged_at"):
            if isinstance(raw.get(key), date):
                raise TypeError(f"{key} must be ISO text")
    if p.exists():
        existing = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            doc = json.loads(existing)
            if not isinstance(doc, dict) or not isinstance(doc.get("changes"), list):
                raise ChangesError(f"{p}: not a planned-change file")
            doc["changes"] = [*doc["changes"], *(dict(r) for r in raws)]
            text = json.dumps(doc, indent=2) + "\n"
        else:
            text = _append_yaml(existing, raws)
    else:
        if p.suffix.lower() == ".json":
            text = (
                json.dumps(
                    {"format": FORMAT, "version": VERSION, "changes": [dict(r) for r in raws]},
                    indent=2,
                )
                + "\n"
            )
        else:
            text = new_file_text(raws)
    result = parse_changes(text, p)  # validates, including duplicate ids
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    return result
