"""Rule backtesting (W3-01): replay a contract over a registry's history.

Every committed version of a name is read the way ``shape check`` reads a profile (a ``.shape``
profile artifact), or, for a share-safe profile, from the statistics that form keeps. A rule the
stored form cannot evaluate is ``not_measured``: it is counted apart and is never a pass. With a
window of ``week`` or ``month`` the versions of each window are merged with
:func:`shape.profile.merge_profiles` and the contract runs on the merged profile; a statistic a
merge leaves unknown makes its rules ``not_measured``.

An incidents file turns the replay into a score: an incident is *caught* when a failing version or
window falls inside it, and a failing version or window outside every incident is an alarm.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

REPORT_FORMAT = "shape-backtest-report"
REPORT_VERSION = 1
INCIDENTS_FORMAT = "shape-incidents"
INCIDENTS_VERSION = 1
WINDOWS = ("day", "week", "month")


class BacktestError(ShapeError, ValueError):
    """The registry, the name, the contract, the dates or a file cannot be used."""


@dataclass
class BacktestResult:
    """The outcome of :func:`backtest`; ``to_dict()`` is the JSON report."""

    report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        copied: dict[str, Any] = json.loads(json.dumps(self.report))
        return copied

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.report["entries"])

    @property
    def missed(self) -> list[str]:
        """The ids of the incidents no failing version or window fell into."""
        return [i["id"] for i in self.report.get("incidents", []) if i["status"] == "missed"]


# -- dates and files ---------------------------------------------------------------------------


def _date(value: Any, what: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise BacktestError(f"{what}: {value!r} is not a date (use YYYY-MM-DD)")


def _read_json(path: str | Path, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise BacktestError(f"{what} {path} is not valid JSON: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise BacktestError(f"{what} {path} is not a text file: {exc}") from exc
    except OSError as exc:
        raise BacktestError(f"{what} {path} cannot be read: {exc.strerror or exc}") from exc


def load_incidents(source: Mapping[str, Any] | str | Path) -> list[dict[str, Any]]:
    """The incidents of an incidents file (a dict or a path), validated, dates as ISO text."""
    doc = _read_json(source, "incidents") if isinstance(source, (str, Path)) else source
    if not isinstance(doc, Mapping) or doc.get("format") != INCIDENTS_FORMAT:
        raise BacktestError(f"an incidents file is a JSON object with format {INCIDENTS_FORMAT!r}")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise BacktestError("the incidents file needs an integer 'version' of 1 or more")
    if version > INCIDENTS_VERSION:
        raise BacktestError(
            f"the incidents file is version {version}; this Shape reads up to version "
            f"{INCIDENTS_VERSION}"
        )
    items = doc.get("incidents")
    if not isinstance(items, list):
        raise BacktestError("the incidents file needs an 'incidents' list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, item in enumerate(items):
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"]:
            raise BacktestError(f"incidents[{n}] is an object with an 'id'")
        if item["id"] in seen:
            raise BacktestError(f"incident id {item['id']!r} appears twice")
        seen.add(item["id"])
        extra = set(item) - {"id", "from", "to", "note"}
        if extra:
            raise BacktestError(f"incident {item['id']!r} has unknown keys: {sorted(extra)}")
        if "from" not in item:
            raise BacktestError(f"incident {item['id']!r} needs a 'from' date")
        start = _date(item["from"], f"incident {item['id']!r} from")
        end = _date(item["to"], f"incident {item['id']!r} to") if item.get("to") else start
        if end < start:
            raise BacktestError(f"incident {item['id']!r} ends ({end}) before it starts ({start})")
        out.append(
            {
                "id": item["id"],
                "from": start.isoformat(),
                "to": end.isoformat(),
                "note": str(item.get("note", "")),
            }
        )
    return out


# -- reading the stored versions ---------------------------------------------------------------


@dataclass
class _Stored:
    content_id: str
    day: dt.date
    kind: str  # "full" | "safe" | "unreadable"
    profile: Any = None
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    is_dataset: bool = False
    reason: str = ""


def _safe_tables(doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    tables = doc.get("tables")
    if not isinstance(tables, Mapping) or not tables:
        raise ValueError("not a safe profile: it has no tables")
    out: dict[str, dict[str, Any]] = {}
    for tname, table in tables.items():
        cols = {}
        for cname, col in (table.get("columns") or {}).items():
            cols[cname] = {k: v for k, v in col.items() if k != "name"} | {"name": cname}
        out[str(tname)] = {
            "name": str(tname),
            "row_count": int(table.get("row_count") or 0),
            "columns": cols,
        }
    return out


def _read_version(raw: bytes, content_id: str, day: dt.date, tables_contract: bool) -> _Stored:
    if raw[:4] == b"PK\x03\x04":
        import warnings

        import shape
        from shape.artifact.io import ArtifactNotVerifiedWarning

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "version.shape"
            path.write_bytes(raw)
            try:
                with warnings.catch_warnings():
                    # the registry verified the content id; there is no signer to name here
                    warnings.simplefilter("ignore", ArtifactNotVerifiedWarning)
                    profile = shape.load(path)
            except (ShapeError, ValueError, OSError) as exc:
                return _Stored(content_id, day, "unreadable", reason=f"not a profile: {exc}")
        return _Stored(
            content_id,
            day,
            "full",
            profile=profile,
            tables=dict(profile.tables),
            is_dataset=profile.is_dataset,
        )
    try:
        doc = json.loads(raw)
        tables = _safe_tables(doc)
    except (ValueError, AttributeError, TypeError) as exc:
        return _Stored(content_id, day, "unreadable", reason=f"not a profile: {exc}")
    return _Stored(
        content_id, day, "safe", tables=tables, is_dataset=tables_contract or len(tables) > 1
    )


def _versions(
    registry: Any, name: str, since: dt.date | None, until: dt.date | None, tables_contract: bool
) -> list[_Stored]:
    from shape.registry.local import LocalRegistry, RegistryError

    reg = registry if isinstance(registry, LocalRegistry) else LocalRegistry(registry)
    try:
        log = reg.log(name)
    except RegistryError as exc:
        raise BacktestError(str(exc)) from exc
    if not log:
        known = ", ".join(reg.names()) or "none yet"
        raise BacktestError(f"nothing is recorded for {name!r} (names in this registry: {known})")
    dated: list[tuple[dt.date, int, dict[str, Any]]] = []
    for n, entry in enumerate(log):
        meta = entry.get("metadata") or {}
        if meta.get("business_date"):
            day = _date(meta["business_date"], f"{name}: the business_date of commit {n + 1}")
        else:
            day = dt.datetime.fromtimestamp(float(entry["created_at"]), dt.UTC).date()
        dated.append((day, n, entry))
    dated.sort(key=lambda item: (item[0], item[1]))
    picked = [
        (d, e)
        for d, _n, e in dated
        if (since is None or d >= since) and (until is None or d <= until)
    ]
    if not picked:
        span = f"{since or 'the start'} to {until or 'the end'}"
        raise BacktestError(f"{name!r} has no committed version from {span}")
    out = []
    for day, entry in picked:
        cid = str(entry["content_id"])
        out.append(_read_version(reg.checkout(name, cid), cid, day, tables_contract))
    return out


# -- windows -------------------------------------------------------------------------------------


def _window_key(day: dt.date, window: str) -> tuple[str, dt.date, dt.date]:
    if window == "week":
        start = day - dt.timedelta(days=day.weekday())
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}", start, start + dt.timedelta(days=6)
    start = day.replace(day=1)
    nxt = (start + dt.timedelta(days=32)).replace(day=1)
    return f"{day.year}-{day.month:02d}", start, nxt - dt.timedelta(days=1)


@dataclass
class _Unit:
    id: str
    window: str
    start: dt.date
    end: dt.date
    members: list[_Stored]


def _units(stored: list[_Stored], window: str) -> list[_Unit]:
    if window == "day":
        return [
            _Unit(f"{s.day.isoformat()}@{s.content_id[:12]}", "day", s.day, s.day, [s])
            for s in stored
        ]
    groups: dict[str, _Unit] = {}
    for s in stored:
        key, start, end = _window_key(s.day, window)
        groups.setdefault(key, _Unit(key, window, start, end, [])).members.append(s)
    return list(groups.values())


def _merged(unit: _Unit) -> tuple[dict[str, dict[str, Any]], bool, bool] | str:
    """The table profiles of a unit: ``(tables, is_dataset, merged)``, or the reason it cannot."""
    from shape.profile.merge import MergeError, merge_profiles

    if unit.window == "day":
        (only,) = unit.members
        if only.kind == "unreadable":
            return only.reason
        return only.tables, only.is_dataset, False
    bad = [m for m in unit.members if m.kind != "full"]
    if bad:
        what = bad[0].reason if bad[0].kind == "unreadable" else "a share-safe version"
        return f"{what} cannot be merged"
    profiles = [m.profile for m in unit.members]
    try:
        both = merge_profiles(profiles, exact_only=any(p.sketches is None for p in profiles))
    except MergeError as exc:
        return f"the versions cannot be merged: {exc}"
    return dict(both.tables), both.is_dataset, True


def _status(failed: list[str], unmeasured: list[str]) -> str:
    if failed:
        return "fail"
    return "not_measured" if unmeasured else "pass"


def _entry(unit: _Unit, contract: Any, old: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from shape.contracts.v1 import ContractError
    from shape.rules.evaluate import FAIL, NOT_MEASURED, evaluate

    days = sorted(m.day for m in unit.members)
    entry: dict[str, Any] = {
        "id": unit.id,
        "window": unit.window,
        "from": unit.start.isoformat(),
        "to": unit.end.isoformat(),
        "first_date": days[0].isoformat(),
        "last_date": days[-1].isoformat(),
        "versions": [m.content_id for m in unit.members],
    }
    got = _merged(unit)
    outcomes = {}
    if isinstance(got, str):
        entry.update(
            status="not_measured", reason=got, rules={"passed": 0, "failed": 0, "not_measured": 0}
        )
        entry.update(failed_rules=[], not_measured_rules=[])
        return entry, None
    tables, dataset, merged = got
    full = all(m.kind == "full" for m in unit.members)
    for label, c in (("new", contract), ("old", old)):
        if c is None:
            continue
        try:
            outcomes[label] = evaluate(tables, dataset, c, merged=merged, full=full)
        except ContractError as exc:
            raise BacktestError(str(exc)) from exc
    new = outcomes["new"]
    failed = [o.id for o in new if o.status == FAIL]
    unmeasured = [o.id for o in new if o.status == NOT_MEASURED]
    entry.update(
        status=_status(failed, unmeasured),
        rules={
            "passed": len(new) - len(failed) - len(unmeasured),
            "failed": len(failed),
            "not_measured": len(unmeasured),
        },
        failed_rules=failed,
        not_measured_rules=unmeasured,
    )
    other = None
    if "old" in outcomes:
        of = [o.id for o in outcomes["old"] if o.status == FAIL]
        ou = [o.id for o in outcomes["old"] if o.status == NOT_MEASURED]
        other = {"status": _status(of, ou), "failed_rules": of}
    return entry, other


def _overlaps(entry: Mapping[str, Any], start: str, end: str) -> bool:
    return not (entry["last_date"] < start or entry["first_date"] > end)


def backtest(
    registry: Any,
    name: str,
    contract: Mapping[str, Any] | str | Path,
    since: Any = None,
    until: Any = None,
    window: str = "day",
    incidents: Mapping[str, Any] | str | Path | None = None,
    compare: Mapping[str, Any] | str | Path | None = None,
) -> BacktestResult:
    """Replay ``contract`` over every committed version of ``name`` in ``registry``.

    ``registry`` is a registry folder or a ``LocalRegistry``. Versions run oldest first, by their
    ``business_date`` metadata, else their commit date; ``since`` and ``until`` (dates, inclusive)
    limit them before any windowing. ``window`` is ``day`` (one entry per version), ``week`` (ISO
    weeks) or ``month``: the versions of a window are merged and the contract runs on the merged
    profile. ``incidents`` (a dict or the path of an incidents file) scores the replay;
    ``compare`` is an older contract to run beside ``contract``.
    """
    from shape.contracts.v1 import ContractError, _load_contract, _validate_contract

    if window not in WINDOWS:
        raise BacktestError(f"the window is one of {', '.join(WINDOWS)}, got {window!r}")
    first = _date(since, "--since") if since is not None else None
    last = _date(until, "--until") if until is not None else None
    if first and last and last < first:
        raise BacktestError(f"--until ({last}) is before --since ({first})")
    try:
        new_doc = _load_contract(dict(contract) if isinstance(contract, Mapping) else contract)
        _validate_contract(new_doc)
        old_doc = None
        if compare is not None:
            old_doc = _load_contract(dict(compare) if isinstance(compare, Mapping) else compare)
            _validate_contract(old_doc)
    except (ContractError, OSError) as exc:
        raise BacktestError(str(exc)) from exc
    planted = load_incidents(incidents) if incidents is not None else None
    stored = _versions(registry, name, first, last, "tables" in new_doc)
    entries = []
    others: list[dict[str, Any] | None] = []
    for unit in _units(stored, window):
        entry, other = _entry(unit, new_doc, old_doc)
        entries.append(entry)
        others.append(other)

    counts = {"pass": 0, "fail": 0, "not_measured": 0}
    rules: dict[str, dict[str, int]] = {}
    for e in entries:
        counts[e["status"]] += 1
        for key, ids in (("failed", e["failed_rules"]), ("not_measured", e["not_measured_rules"])):
            for rid in ids:
                rules.setdefault(rid, {"failed": 0, "not_measured": 0})[key] += 1
    report: dict[str, Any] = {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "name": name,
        "window": window,
        "since": first.isoformat() if first else None,
        "until": last.isoformat() if last else None,
        "summary": {"entries": len(entries), **counts},
        "entries": entries,
        "rules": rules,
    }
    if planted is not None:
        report.update(_score_incidents(planted, entries))
    if old_doc is not None:
        diffs = [
            {
                "id": e["id"],
                "status": e["status"],
                "old_status": o["status"],
                "failed_rules": e["failed_rules"],
                "old_failed_rules": o["failed_rules"],
            }
            for e, o in zip(entries, others, strict=True)
            if o is not None and o["status"] != e["status"]
        ]
        report["compare"] = {"disagreements": len(diffs), "entries": diffs}
    return BacktestResult(report)


def _score_incidents(
    planted: list[dict[str, Any]], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    scored = []
    for inc in planted:
        inside = [e for e in entries if _overlaps(e, inc["from"], inc["to"])]
        caught = [e["id"] for e in inside if e["status"] == "fail"]
        scored.append(
            {
                **inc,
                "status": "caught" if caught else "missed",
                "first_caught_by": caught[0] if caught else None,
                "caught_by": caught,
                "entries_inside": len(inside),
            }
        )
    alarms = [
        e["id"]
        for e in entries
        if e["status"] == "fail" and not any(_overlaps(e, i["from"], i["to"]) for i in planted)
    ]
    return {
        "incidents": scored,
        "incident_summary": {
            "caught": sum(1 for i in scored if i["status"] == "caught"),
            "missed": sum(1 for i in scored if i["status"] == "missed"),
        },
        "alarms_outside_incidents": alarms,
    }
