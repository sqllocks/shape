"""Resolve a source's baseline against the registry (``shape registry``).

Kinds, with D the reference date (``as_of``, default today in UTC) and an entry's date its
``business_date`` metadata, else the UTC date it was committed:

* ``previous_run``: the newest commit (before D when ``as_of`` is given);
* ``same_weekday``: the newest entry before D that falls on D's weekday;
* ``rolling_window``: the ``window`` newest entries before D (fewer when fewer exist);
* ``month_end``: the newest entry in the month before D's, up to that month's last day;
* ``pinned``: a ``.shape`` file (``artifact``) or a registry ref, tag or content id (``ref``).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from shape.project.file import Project, ProjectError
from shape.registry.local import LocalRegistry, RegistryError


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """One resolved baseline: ``path`` is a file to read (a pinned artifact, or the registry
    object written under the work folder)."""

    path: str
    content_id: str | None = None
    date: str | None = None
    artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"date": self.date, "content_id": self.content_id, "artifact": self.artifact}
        return {k: v for k, v in out.items() if v is not None}


@dataclass(frozen=True, slots=True)
class ResolvedBaseline:
    kind: str
    entries: tuple[BaselineEntry, ...]
    window: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "entries": [e.to_dict() for e in self.entries]}
        if self.window is not None:
            out["window"] = self.window
        return out


def _entry_date(entry: dict[str, Any]) -> date:
    given = (entry.get("metadata") or {}).get("business_date")
    if isinstance(given, str):
        try:
            return date.fromisoformat(given)
        except ValueError:
            pass
    return datetime.fromtimestamp(float(entry.get("created_at", 0)), UTC).date()


def _previous_month(day: date) -> tuple[date, date]:
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _write(reg: LocalRegistry, name: str, content_id: str, workdir: Path) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / f"{name}-{content_id[:12]}.shape"
    target.write_bytes(reg.checkout(name, content_id))
    return str(target)


def resolve_baseline(
    project: Project,
    source_name: str,
    *,
    as_of: date | None = None,
    workdir: str | Path,
) -> ResolvedBaseline:
    """The baseline of ``source_name``, newest first. Registry objects are written under
    ``workdir``; the registry is never created or changed."""
    source = project.source(source_name)
    b = source.baseline
    if b is None:
        raise ProjectError(f"source {source_name!r} declares no baseline in {project.path}")
    work = Path(workdir)
    if b.kind == "pinned" and b.artifact is not None:
        if not Path(b.artifact).is_file():
            raise ProjectError(f"pinned artifact not found: {b.artifact}")
        return ResolvedBaseline("pinned", (BaselineEntry(b.artifact, artifact=b.artifact),))
    root = Path(b.registry)
    reg = LocalRegistry(root) if (root / "logs").is_dir() else None
    log = reg.log(b.name) if reg else []
    if reg is None or (not log and b.kind != "pinned"):
        raise ProjectError(
            f"source {source_name!r} has no entries for {b.name!r} in the baseline registry {root}"
        )
    assert reg is not None
    if b.kind == "pinned":
        try:
            content_id = reg.resolve(b.name, b.ref or "")
            entry = reg.entry(b.name, b.ref or "")
        except RegistryError:
            raise ProjectError(
                f"{b.name}@{b.ref} is not recorded in the baseline registry {root}"
            ) from None
        found = BaselineEntry(
            _write(reg, b.name, content_id, work), content_id, _entry_date(entry).isoformat()
        )
        return ResolvedBaseline("pinned", (found,))

    dated = [(_entry_date(e), i, e) for i, e in enumerate(log)]
    reference = as_of or datetime.now(UTC).date()
    before = [t for t in dated if t[0] < reference]
    chosen: list[tuple[date, int, dict[str, Any]]]
    if b.kind == "previous_run":
        pool = before if as_of else dated
        if not pool:
            raise ProjectError(f"no entries for {b.name!r} before {reference} in {root}")
        chosen = [max(pool, key=lambda t: t[1])]
    elif b.kind == "same_weekday":
        same = [t for t in before if t[0].weekday() == reference.weekday()]
        if not same:
            raise ProjectError(
                f"no earlier {calendar.day_name[reference.weekday()]} entry for {b.name!r} "
                f"before {reference} in {root}"
            )
        chosen = [max(same, key=lambda t: (t[0], t[1]))]
    elif b.kind == "rolling_window":
        if not before:
            raise ProjectError(f"no entries for {b.name!r} before {reference} in {root}")
        newest = sorted(before, key=lambda t: (t[0], t[1]), reverse=True)
        chosen = newest[: b.window or 1]
    else:  # month_end
        first, last = _previous_month(reference)
        month = [t for t in dated if first <= t[0] <= last]
        if not month:
            raise ProjectError(
                f"no entry for {b.name!r} in {first:%Y-%m} (the month before {reference}) in {root}"
            )
        chosen = [max(month, key=lambda t: (t[0], t[1]))]
    entries = tuple(
        BaselineEntry(_write(reg, b.name, e["content_id"], work), e["content_id"], d.isoformat())
        for d, _, e in chosen
    )
    return ResolvedBaseline(b.kind, entries, b.window if b.kind == "rolling_window" else None)
