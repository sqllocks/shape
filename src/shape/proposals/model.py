"""The decision file: proposals with evidence and confidence, and a person's decisions on them.

A decision file is JSON text meant to be committed. It declares ``format`` and an integer
``version``; keys are sorted, proposals and decisions are ordered by id, times are UTC ISO 8601
(``2026-10-03T12:00:00Z``) and confidences are rounded to four places, so a re-profile that finds
the same things changes no line and one decision changes only its own lines.

A file that holds no contract rule is written as version 1; a file with a ``rule`` proposal is
written as version 2, which adds the decision status ``stale`` (an accepted rule whose evidence no
longer holds after a re-profile). Both versions are read.

Rules the file keeps (``docs/PROPOSALS.md``):

* a proposal is identified by ``KIND:SUBJECT`` and not by its evidence, so a re-profile updates
  the evidence of the same proposal and the decision on it stays attached;
* a rejected proposal is never proposed again, whatever the new evidence;
* nothing is accepted automatically unless ``update(auto_accept=THRESHOLD)`` is asked for.
"""

from __future__ import annotations

import builtins
import copy
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
VERSION = 1  # written for a file with no rule proposal, byte for byte as before W3-02
RULES_VERSION = 2  # written for a file with a rule proposal; adds the status "stale"
MAX_VERSION = RULES_VERSION  # the newest version this Shape reads

DEFAULT_KINDS = ("relationship", "pii", "semantic")  # what a run proposes unless asked otherwise
KINDS = (*DEFAULT_KINDS, "rule", "type")  # ``rule`` and ``type`` are proposed only when asked for
STATUSES = ("accepted", "rejected", "deferred")  # what a person decides
STALE = "stale"  # set by a re-profile, never by a person (version 2)
PENDING = "pending"
RULE = "rule"
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


class RuleConflictError(DecisionError):
    """An accepted rule disagrees with a rule already in the contract it is merged into."""


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
    stale: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": len(self.added),
            "updated": len(self.updated),
            "withdrawn": len(self.withdrawn),
            "skipped_rejected": list(self.skipped_rejected),
            "auto_accepted": list(self.auto_accepted),
            "stale": list(self.stale),
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
        if version > MAX_VERSION:
            raise DecisionError(
                f"this decision file is version {version}, which is newer than the version "
                f"{MAX_VERSION} this Shape reads; upgrade Shape to read it"
            )
        unknown = sorted(set(doc) - _TOP_KEYS)
        if unknown:
            raise DecisionError(f"unknown key {unknown[0]!r} in the decision file")
        for key in ("proposals", "decisions"):
            if not isinstance(doc.get(key), list):
                raise DecisionError(f"missing key {key!r} (a list)")
        out = cls()
        for i, raw in enumerate(doc["proposals"]):
            p = cls._read_proposal(raw, f"proposals[{i}]", version)
            if p.id in out._proposals:
                raise DecisionError(f"duplicate proposal {p.id!r}")
            out._proposals[p.id] = p
        for i, raw in enumerate(doc["decisions"]):
            d = cls._read_decision(raw, f"decisions[{i}]", version)
            if d.proposal not in out._proposals:
                raise DecisionError(f"decisions[{i}]: no proposal {d.proposal!r} to decide")
            if d.proposal in out._decisions:
                raise DecisionError(f"proposal {d.proposal!r} is decided twice")
            out._decisions[d.proposal] = d
        return out

    @staticmethod
    def _read_proposal(raw: Any, where: str, version: int = MAX_VERSION) -> Proposal:
        if not isinstance(raw, dict):
            raise DecisionError(f"{where}: must be an object")
        unknown = sorted(set(raw) - _PROPOSAL_KEYS)
        if unknown:
            raise DecisionError(f"{where}: unknown key {unknown[0]!r}")
        for key in ("id", "kind", "subject", "claim", "confidence", "evidence", "proposed_at"):
            if key not in raw:
                raise DecisionError(f"{where}: missing key {key!r}")
        pid, kind, subject = raw["id"], raw["kind"], raw["subject"]
        # version 1 holds every kind but ``rule`` (W2-07's ``type`` kept the version at 1)
        allowed_kinds = (
            KINDS if version >= RULES_VERSION else tuple(k for k in KINDS if k != "rule")
        )
        if kind not in allowed_kinds:
            hint = " (rule proposals need version 2)" if kind == RULE else ""
            raise DecisionError(
                f"{where}: kind must be one of {', '.join(allowed_kinds)}, got {kind!r}{hint}"
            )
        _check_identity(pid, kind, subject, raw["claim"], raw["evidence"], where)
        when = raw["proposed_at"]
        if not _is_time(when):
            raise DecisionError(f"{where}: proposed_at must be UTC like 2026-10-03T12:00:00Z")
        return Proposal(
            pid, kind, subject, raw["claim"], _check_confidence(raw["confidence"], where),
            raw["evidence"], when,
        )  # fmt: skip

    @staticmethod
    def _read_decision(raw: Any, where: str, version: int = MAX_VERSION) -> Decision:
        if not isinstance(raw, dict):
            raise DecisionError(f"{where}: must be an object")
        unknown = sorted(set(raw) - _DECISION_KEYS)
        if unknown:
            raise DecisionError(f"{where}: unknown key {unknown[0]!r}")
        for key in ("proposal", "status", "actor", "at", "note"):
            if not isinstance(raw.get(key), str):
                raise DecisionError(f"{where}: {key!r} must be a string")
        allowed = STATUSES if version < RULES_VERSION else (*STATUSES, STALE)
        if raw["status"] not in allowed:
            raise DecisionError(
                f"{where}: status must be one of {', '.join(allowed)}, got {raw['status']!r}"
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

    @property
    def version(self) -> int:
        """The version this file is written as: 2 when it holds a rule proposal, else 1."""
        has_rule = any(p.kind == RULE for p in self._proposals.values())
        return RULES_VERSION if has_rule else VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": self.version,
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
        if status is not None and status not in (PENDING, *STATUSES, STALE):
            raise DecisionError(f"status must be pending or one of {', '.join((*STATUSES, STALE))}")
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

    # ---- the contract ---------------------------------------------------------------------

    def to_contract(
        self, merge: Mapping[str, Any] | None = None, merge_source: str = "the existing contract"
    ) -> dict[str, Any]:
        """A contract v1 from the accepted, non-stale ``rule`` proposals, in proposal id order.

        With ``merge`` (an existing contract, left unchanged) the rules are added to a copy of it.
        An accepted rule that disagrees with a rule already there raises
        :class:`RuleConflictError`, naming both (``merge_source`` is how the message calls the
        existing contract). A rule that agrees is not a conflict. Written with sorted keys (see
        :func:`dump_contract`), the same decisions give the same bytes.
        """
        entries = sorted(self.accepted(RULE), key=lambda e: e.proposal.id)
        if not entries:
            raise DecisionError("no accepted, non-stale rule proposal in the decision file")
        if merge is not None and not isinstance(merge, Mapping):
            raise DecisionError("the contract to merge into must be a JSON object")
        if merge is not None:
            _check_existing(merge, merge_source)
        out: dict[str, Any] = copy.deepcopy(dict(merge)) if merge is not None else {}
        for e in entries:
            _place(out, e.proposal, merge_source)
        return out

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
        the same proposal replaces the first (a stale rule is accepted again this way)."""
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
        an undecided proposal of ``kinds`` (default: the kinds ``propose`` runs by default;
        ``rule`` and ``type`` are proposed only when asked for) that the run no longer finds is
        withdrawn. An *accepted* rule that the run no longer finds, or finds with a different
        claim, is marked ``stale`` and keeps its claim and evidence; it is never dropped and
        never changed under the person who accepted it.
        With ``auto_accept`` a threshold from 0 to 1, undecided proposals at or above it are
        accepted by the actor ``auto-accept``; without it nothing is accepted automatically.
        """
        if auto_accept is not None and (
            isinstance(auto_accept, bool)
            or not isinstance(auto_accept, (int, float))
            or not 0.0 <= auto_accept <= 1.0
        ):
            raise DecisionError("the auto-accept threshold must be a number from 0 to 1")
        run_kinds = set(DEFAULT_KINDS if kinds is None else kinds)
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
        stale: list[str] = []
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
                if (
                    p.kind == RULE
                    and self._decisions[p.id].status == "accepted"
                    and known.claim != p.claim
                ):
                    self._go_stale(p.id, at, "the rule proposed from the new evidence differs")
                    stale.append(p.id)
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
        for i, p in self._proposals.items():
            d = self._decisions.get(i)
            if p.kind == RULE and RULE in run_kinds and i not in seen and d is not None:
                if d.status == "accepted":
                    self._go_stale(i, at, "the new evidence no longer supports the rule")
                    stale.append(i)
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
            tuple(sorted(stale)),
        )

    def _go_stale(self, proposal_id: str, at: str, reason: str) -> None:
        """Replace an accepted decision by a ``stale`` one that remembers who accepted it, when,
        and why it went stale."""
        old = self._decisions[proposal_id]
        note = f"{reason}; accepted by {old.actor} at {old.at}"
        if old.note:
            note += f" ({old.note})"
        self._decisions[proposal_id] = Decision(proposal_id, STALE, old.actor, at, note)


def dump_contract(contract: Mapping[str, Any]) -> str:
    """The contract as text: keys sorted, two-space indent, one final newline."""
    return json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _split(p: Proposal) -> tuple[str, dict[str, Any]]:
    """(table, the table-level contract body) of a rule proposal's claim."""
    claim = p.claim
    if "tables" in claim:
        ((table, body),) = claim["tables"].items()
        return table, body
    body = claim
    if "row_count" in body:
        suffix = ".row_count"
    elif "fd" in body:
        rule = body["fd"][0]
        det = rule["determinant"]
        det = [det] if isinstance(det, str) else det
        suffix = f".fd.{','.join(det)}->{rule['dependent']}"
    elif "reference_pair" in body:
        rule = body["reference_pair"][0]
        suffix = f".reference_pair.{','.join(rule['columns'])}->{rule['reference']}"
    else:
        (column,) = body["columns"]
        suffix = f".{column}.{p.subject.rsplit('.', 1)[1]}"
    return p.subject[: len(p.subject) - len(suffix)], body


def _need_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionError(f"{where} in the contract to merge into must be an object")
    return value


def _place(out: dict[str, Any], p: Proposal, source: str) -> None:
    """Add the rule ``p`` to the contract ``out``; a rule that differs from one there conflicts."""
    table, body = _split(p)

    def conflict(location: str) -> RuleConflictError:
        return RuleConflictError(f"{p.id} conflicts with {location} in {source}")

    if "tables" in p.claim:
        if "columns" in out or "row_count" in out:
            raise DecisionError(
                f"{p.id} is for a multi-table profile but the contract to merge into holds one "
                "table's rules"
            )
        tables = _need_object(out.setdefault("tables", {}), "'tables'")
        target = _need_object(tables.setdefault(table, {}), f"table {table!r}")
    else:
        if "tables" in out:
            raise DecisionError(
                f"{p.id} is for a single-table profile but the contract to merge into has 'tables'"
            )
        target = out
    if "row_count" in body:
        band = _need_object(target.setdefault("row_count", {}), "'row_count'")
        for key, value in body["row_count"].items():
            if key in band and band[key] != value:
                raise conflict(f"{table}.row_count.{key}")
            band[key] = value
    for column, rules in body.get("columns", {}).items():
        columns = _need_object(target.setdefault("columns", {}), "'columns'")
        have = _need_object(columns.setdefault(column, {}), f"column {column!r}")
        for name, value in rules.items():
            if name in have and have[name] != value:
                raise conflict(f"{table}.{column}.{name}")
            have[name] = value
    for key, same in (
        ("fd", ("determinant", "dependent")),
        ("reference_pair", ("columns", "reference")),
    ):
        for rule in body.get(key, ()):
            listed = target.setdefault(key, [])
            if not isinstance(listed, list):
                raise DecisionError(f"{key!r} in the contract to merge into must be a list")
            for old in listed:
                if all(old.get(k) == rule[k] for k in same):
                    if old != rule:
                        first = rule[same[0]]
                        label = ",".join(first if isinstance(first, list) else [first])
                        raise conflict(f"{table}.{key}.{label}->{rule[same[1]]}")
                    break
            else:
                listed.append(rule)


def _check_existing(contract: Mapping[str, Any], source: str) -> None:
    """The contract to merge into is read as ``shape check`` reads it: a newer version, another
    format or an unknown key is a :class:`DecisionError` with ``shape check``'s message, never
    merged and written back (#645)."""
    from shape.contracts.v1 import ContractError, _validate_contract

    doc = dict(contract)
    try:
        _validate_contract(doc)
        tables = doc.get("tables")
        if isinstance(tables, Mapping):
            for name, sub in tables.items():
                if not isinstance(sub, dict):
                    raise ContractError(f"table {name!r} of 'tables' must be a contract object")
                _validate_contract(sub, top=False)
    except ContractError as exc:
        raise DecisionError(f"{source}: {exc}") from exc
