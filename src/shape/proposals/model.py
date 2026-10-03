"""The decision file: proposals with evidence and confidence, and a person's decisions on them.

A decision file is JSON text meant to be committed. It declares ``format`` and an integer
``version``; keys are sorted, proposals and decisions are ordered by id, times are UTC ISO 8601
(``2026-10-03T12:00:00Z``) and confidences are rounded to four places, so a re-profile that finds
the same things changes no line and one decision changes only its own lines.

Rules the file keeps (``docs/PROPOSALS.md``):

* a proposal is identified by ``KIND:SUBJECT`` and not by its evidence, so a re-profile updates
  the evidence of the same proposal and the decision on it stays attached;
* a rejected proposal is never proposed again, whatever the new evidence;
* nothing is accepted automatically unless ``update(auto_accept=THRESHOLD)`` is asked for.
"""

from __future__ import annotations

import builtins
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

FORMAT = "shape-decisions"
VERSION = 1

KINDS = ("relationship", "pii", "semantic")
STATUSES = ("accepted", "rejected", "deferred")
PENDING = "pending"
AUTO_ACCEPT_ACTOR = "auto-accept"

_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


def _is_time(text: Any) -> bool:
    """A real UTC time written like ``2026-10-03T12:00:00Z`` (not ``2026-02-30T...``)."""
    if not isinstance(text, str) or not _TIME.match(text):
        return False
    try:
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


_TOP_KEYS = {"format", "version", "proposals", "decisions"}
_PROPOSAL_KEYS = {"id", "kind", "subject", "claim", "confidence", "evidence", "proposed_at"}
_DECISION_KEYS = {"proposal", "status", "actor", "at", "note"}


class DecisionError(ShapeError, ValueError):
    """A decision file or a decision that Shape cannot use."""


@dataclass(frozen=True, eq=True)
class Proposal:
    """One question for a person: ``claim`` is what Shape thinks is true, ``evidence`` why."""

    id: str
    kind: str
    subject: str
    claim: dict[str, Any]
    confidence: float
    evidence: dict[str, Any] = field(default_factory=dict)
    proposed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "claim": self.claim,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }
        if self.proposed_at is not None:
            out["proposed_at"] = self.proposed_at
        return out


@dataclass(frozen=True, eq=True)
class Decision:
    proposal: str
    status: str
    actor: str
    at: str
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "proposal": self.proposal,
            "status": self.status,
            "actor": self.actor,
            "at": self.at,
            "note": self.note,
        }


@dataclass(frozen=True)
class Entry:
    """A proposal and the decision on it, if any."""

    proposal: Proposal
    decision: Decision | None = None

    @property
    def status(self) -> str:
        return self.decision.status if self.decision else PENDING

    def to_dict(self) -> dict[str, Any]:
        out = self.proposal.to_dict()
        out["status"] = self.status
        if self.decision is not None:
            out["decision"] = self.decision.to_dict()
        return out


@dataclass(frozen=True)
class UpdateResult:
    """What ``DecisionFile.update`` changed, as sorted proposal ids."""

    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    withdrawn: tuple[str, ...] = ()
    skipped_rejected: tuple[str, ...] = ()
    auto_accepted: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "withdrawn": len(self.withdrawn),
            "skipped_rejected": list(self.skipped_rejected),
            "auto_accepted": list(self.auto_accepted),
        }


def stamp(now: datetime | str | None = None) -> str:
    """A time as UTC ISO 8601 with whole seconds and a ``Z``."""
    if now is None:
        now = datetime.now(UTC)
    if isinstance(now, str):
        if not _is_time(now):
            raise DecisionError(f"a time must look like 2026-10-03T12:00:00Z, got {now!r}")
        return now
    if now.tzinfo is None:
        raise DecisionError("a time must carry a time zone")
    return now.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon(value: Any) -> Any:
    """JSON-safe, floats rounded to four places, so equal evidence compares and prints equal."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecisionError("evidence cannot hold NaN or infinity")
        return round(value, 4)
    if isinstance(value, Mapping):
        return {str(k): _canon(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    raise DecisionError(f"evidence holds a {type(value).__name__}, which is not JSON")


_STRUCTURE = ("format", "version", "proposals", "decisions")


def _sorted_deep(value: Any, top: bool = True) -> Any:
    """Keys sorted at every depth, except the top level, whose order is fixed (``_STRUCTURE``)."""
    if isinstance(value, dict):
        if top:
            return {k: _sorted_deep(value[k], False) for k in _STRUCTURE if k in value}
        return {k: _sorted_deep(value[k], False) for k in sorted(value)}
    if isinstance(value, list):
        return [_sorted_deep(v, False) for v in value]
    return value


def _check_confidence(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError(f"{where}: confidence must be a number from 0 to 1")
    if not 0.0 <= value <= 1.0:  # NaN fails too
        raise DecisionError(f"{where}: confidence must be from 0 to 1, got {value!r}")
    return float(value)


def _check_identity(
    pid: Any, kind: Any, subject: Any, claim: Any, evidence: Any, where: str
) -> None:
    """The rules a proposal's id, kind, subject, claim and evidence follow, on read and on
    update alike, so nothing is stored that the file could not be read back with."""
    if kind not in KINDS:
        raise DecisionError(f"{where}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if not isinstance(subject, str) or not subject:
        raise DecisionError(f"{where}: subject must be a non-empty string")
    if pid != f"{kind}:{subject}":
        raise DecisionError(f"{where}: id {pid!r} must be {kind}:{subject}")
    if not isinstance(claim, Mapping) or not isinstance(evidence, Mapping):
        raise DecisionError(f"{where}: claim and evidence must be objects")


class DecisionFile:
    """Proposals and decisions, read from and written to a text file."""

    def __init__(self) -> None:
        self._proposals: dict[str, Proposal] = {}
        self._decisions: dict[str, Decision] = {}

    @classmethod
    def empty(cls) -> DecisionFile:
        return cls()

    # ---- reading and writing ------------------------------------------------------------

    @classmethod
    def from_dict(cls, doc: Any) -> DecisionFile:
        if not isinstance(doc, dict):
            raise DecisionError("a decision file must be a JSON object")
        if doc.get("format") != FORMAT:
            raise DecisionError(f"not a decision file: expected format {FORMAT!r}")
        version = doc.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise DecisionError(f"the version must be an integer of 1 or more, got {version!r}")
        if version > VERSION:
            raise DecisionError(
                f"this decision file is version {version}, which is newer than the version "
                f"{VERSION} this Shape reads; upgrade Shape to read it"
            )
        unknown = sorted(set(doc) - _TOP_KEYS)
        if unknown:
            raise DecisionError(f"unknown key {unknown[0]!r} in the decision file")
        for key in ("proposals", "decisions"):
            if not isinstance(doc.get(key), list):
                raise DecisionError(f"missing key {key!r} (a list)")
        out = cls()
        for i, raw in enumerate(doc["proposals"]):
            p = cls._read_proposal(raw, f"proposals[{i}]")
            if p.id in out._proposals:
                raise DecisionError(f"duplicate proposal {p.id!r}")
            out._proposals[p.id] = p
        for i, raw in enumerate(doc["decisions"]):
            d = cls._read_decision(raw, f"decisions[{i}]")
            if d.proposal not in out._proposals:
                raise DecisionError(f"decisions[{i}]: no proposal {d.proposal!r} to decide")
            if d.proposal in out._decisions:
                raise DecisionError(f"proposal {d.proposal!r} is decided twice")
            out._decisions[d.proposal] = d
        return out

    @staticmethod
    def _read_proposal(raw: Any, where: str) -> Proposal:
        if not isinstance(raw, dict):
            raise DecisionError(f"{where}: must be an object")
        unknown = sorted(set(raw) - _PROPOSAL_KEYS)
        if unknown:
            raise DecisionError(f"{where}: unknown key {unknown[0]!r}")
        for key in ("id", "kind", "subject", "claim", "confidence", "evidence", "proposed_at"):
            if key not in raw:
                raise DecisionError(f"{where}: missing key {key!r}")
        pid, kind, subject = raw["id"], raw["kind"], raw["subject"]
        _check_identity(pid, kind, subject, raw["claim"], raw["evidence"], where)
        when = raw["proposed_at"]
        if not _is_time(when):
            raise DecisionError(f"{where}: proposed_at must be UTC like 2026-10-03T12:00:00Z")
        return Proposal(
            pid, kind, subject, raw["claim"], _check_confidence(raw["confidence"], where),
            raw["evidence"], when,
        )  # fmt: skip

    @staticmethod
    def _read_decision(raw: Any, where: str) -> Decision:
        if not isinstance(raw, dict):
            raise DecisionError(f"{where}: must be an object")
        unknown = sorted(set(raw) - _DECISION_KEYS)
        if unknown:
            raise DecisionError(f"{where}: unknown key {unknown[0]!r}")
        for key in ("proposal", "status", "actor", "at", "note"):
            if not isinstance(raw.get(key), str):
                raise DecisionError(f"{where}: {key!r} must be a string")
        if raw["status"] not in STATUSES:
            raise DecisionError(
                f"{where}: status must be one of {', '.join(STATUSES)}, got {raw['status']!r}"
            )
        if not raw["actor"].strip():
            raise DecisionError(f"{where}: actor must not be empty")
        if not _is_time(raw["at"]):
            raise DecisionError(f"{where}: at must be UTC like 2026-10-03T12:00:00Z")
        return Decision(raw["proposal"], raw["status"], raw["actor"], raw["at"], raw["note"])

    @classmethod
    def loads(cls, text: str) -> DecisionFile:
        try:
            doc = json.loads(text)
        except ValueError as exc:
            raise DecisionError(f"not valid JSON: {exc}") from exc
        return cls.from_dict(doc)

    @classmethod
    def read(cls, path: str | Path) -> DecisionFile:
        p = Path(path)
        try:
            return cls.loads(p.read_text(encoding="utf-8"))
        except DecisionError as exc:
            raise DecisionError(f"{p}: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "proposals": [self._proposals[i].to_dict() for i in sorted(self._proposals)],
            "decisions": [self._decisions[i].to_dict() for i in sorted(self._decisions)],
        }

    def dumps(self) -> str:
        """The canonical text: ``format`` and ``version`` first, then the proposals and the
        decisions in id order, the keys of claims and evidence sorted, two-space indent, one final
        newline."""
        return json.dumps(_sorted_deep(self.to_dict()), indent=2, ensure_ascii=False) + "\n"

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.dumps().encode("utf-8"))

    # ---- querying -----------------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Every proposal with its decision, ordered by id."""
        return [Entry(self._proposals[i], self._decisions.get(i)) for i in sorted(self._proposals)]

    def list(
        self,
        status: str | None = None,
        kind: str | None = None,
        min_confidence: float | None = None,
    ) -> list[Entry]:
        """Entries filtered by ``status`` (``pending`` or a decision), ``kind`` and confidence,
        the most confident first."""
        if status is not None and status not in (PENDING, *STATUSES):
            raise DecisionError(f"status must be pending or one of {', '.join(STATUSES)}")
        if kind is not None and kind not in KINDS:
            raise DecisionError(f"kind must be one of {', '.join(KINDS)}")
        rows = [
            e
            for e in self.entries()
            if (status is None or e.status == status)
            and (kind is None or e.proposal.kind == kind)
            and (min_confidence is None or e.proposal.confidence >= min_confidence)
        ]
        return sorted(rows, key=lambda e: (-e.proposal.confidence, e.proposal.id))

    def accepted(self, kind: str | None = None) -> builtins.list[Entry]:
        return self.list(status="accepted", kind=kind)

    def rejected(self, kind: str | None = None) -> builtins.list[Entry]:
        return self.list(status="rejected", kind=kind)

    # ---- changing -----------------------------------------------------------------------

    def decide(
        self,
        proposal_id: str,
        status: str,
        *,
        actor: str,
        note: str = "",
        now: datetime | str | None = None,
    ) -> Decision:
        """Record that ``actor`` accepted, rejected or deferred a proposal. A second decision on
        the same proposal replaces the first."""
        if status not in STATUSES:
            raise DecisionError(f"status must be one of {', '.join(STATUSES)}, got {status!r}")
        if proposal_id not in self._proposals:
            raise DecisionError(f"no proposal {proposal_id!r} in the decision file")
        if not isinstance(actor, str) or not actor.strip():
            raise DecisionError("a decision needs an actor (who decided)")
        if not isinstance(note, str):
            raise DecisionError(f"a decision's note must be text, got {type(note).__name__}")
        d = Decision(proposal_id, status, actor.strip(), stamp(now), note)
        self._decisions[proposal_id] = d
        return d

    def update(
        self,
        proposals: Iterable[Proposal],
        *,
        kinds: Iterable[str] | None = None,
        auto_accept: float | None = None,
        now: datetime | str | None = None,
    ) -> UpdateResult:
        """Merge the proposals of a new run.

        New proposals are added; a known one has its claim, confidence and evidence updated when
        they changed (its first ``proposed_at`` and its decision stay); a rejected one is skipped;
        an undecided proposal of ``kinds`` (default all) that the run no longer finds is withdrawn.
        With ``auto_accept`` a threshold from 0 to 1, undecided proposals at or above it are
        accepted by the actor ``auto-accept``; without it nothing is accepted automatically.
        """
        if auto_accept is not None and (
            isinstance(auto_accept, bool)
            or not isinstance(auto_accept, (int, float))
            or not 0.0 <= auto_accept <= 1.0
        ):
            raise DecisionError("the auto-accept threshold must be a number from 0 to 1")
        run_kinds = set(KINDS if kinds is None else kinds)
        bad = run_kinds - set(KINDS)
        if bad:
            raise DecisionError(f"unknown kind {sorted(bad)[0]!r}")
        at = stamp(now)
        incoming = list(proposals)
        for raw in incoming:  # every check before any change: a bad proposal changes nothing
            _check_identity(raw.id, raw.kind, raw.subject, raw.claim, raw.evidence, str(raw.id))
            _canon(raw.claim)
            _canon(raw.evidence)
            _check_confidence(raw.confidence, str(raw.id))
        added: list[str] = []
        updated: list[str] = []
        skipped: list[str] = []
        auto: list[str] = []
        seen: set[str] = set()
        for raw in incoming:
            p = Proposal(
                raw.id,
                raw.kind,
                raw.subject,
                _canon(raw.claim),
                round(_check_confidence(raw.confidence, raw.id), 4),
                _canon(raw.evidence),
            )
            seen.add(p.id)
            known = self._proposals.get(p.id)
            if known is not None and self._decisions.get(p.id, None) is not None:
                if self._decisions[p.id].status == "rejected":
                    skipped.append(p.id)
                    continue
            if known is None:
                self._proposals[p.id] = Proposal(
                    p.id, p.kind, p.subject, p.claim, p.confidence, p.evidence, at
                )
                added.append(p.id)
            elif (known.claim, known.confidence, known.evidence) != (
                p.claim,
                p.confidence,
                p.evidence,
            ):
                self._proposals[p.id] = Proposal(
                    p.id, p.kind, p.subject, p.claim, p.confidence, p.evidence, known.proposed_at
                )
                updated.append(p.id)
            if (
                auto_accept is not None
                and p.id not in self._decisions
                and p.confidence >= auto_accept
            ):
                self._decisions[p.id] = Decision(
                    p.id,
                    "accepted",
                    AUTO_ACCEPT_ACTOR,
                    at,
                    f"confidence {p.confidence} is at or above the auto-accept threshold "
                    f"{auto_accept}",
                )
                auto.append(p.id)
        withdrawn = sorted(
            i
            for i, p in self._proposals.items()
            if p.kind in run_kinds and i not in seen and i not in self._decisions
        )
        for i in withdrawn:
            del self._proposals[i]
        return UpdateResult(
            tuple(sorted(added)),
            tuple(sorted(updated)),
            tuple(withdrawn),
            tuple(sorted(skipped)),
            tuple(sorted(auto)),
        )
