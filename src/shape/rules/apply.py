"""Apply accepted rule proposals to a contract without replacing existing rules."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from shape.proposals import DecisionError, DecisionFile, dump_contract


class RuleWriteError(OSError):
    """Writing the staged contract failed, leaving the previous contract intact."""


def apply_rules(decisions: DecisionFile, contract: str | Path) -> dict[str, Any]:
    """Merge accepted rules, counting newly added and explicitly rejected proposals.

    Pending, deferred and stale proposals are ignored. A conflicting accepted rule
    refuses the entire operation before any file is written. Reapplying unchanged
    decisions leaves the contract bytes unchanged and reports zero applied rules.
    """
    from shape.contracts.v1 import validate_contract

    path = Path(contract)
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise DecisionError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(existing, dict):
            raise DecisionError(f"{path}: the contract must be a JSON object")
        validate_contract(existing)
    current = existing
    applied = 0
    for entry in sorted(decisions.accepted("rule"), key=lambda e: e.proposal.id):
        claim = entry.proposal.claim
        validate_contract(claim)
        body = claim
        if "tables" in claim:
            if set(claim) != {"tables"} or len(claim["tables"]) != 1:
                raise DecisionError(f"{entry.proposal.id}: a rule claim must name one table")
            body = next(iter(claim["tables"].values()))
        if (
            len(body) != 1
            or not set(body) <= {"row_count", "columns", "fd", "reference_pair"}
            or ("columns" in body and len(body["columns"]) != 1)
            or any(key in body and len(body[key]) != 1 for key in ("fd", "reference_pair"))
        ):
            raise DecisionError(f"{entry.proposal.id}: expected one supported contract rule")
        one = DecisionFile.from_dict(
            {
                "format": "shape-decisions",
                "version": 2,
                "proposals": [entry.proposal.to_dict()],
                "decisions": [entry.decision.to_dict()] if entry.decision else [],
            }
        )
        merged = one.to_contract(current, merge_source=str(path))
        applied += merged != current
        current = merged
    validate_contract(current)
    if applied:
        # Stage in the same directory: a failed write cannot truncate the old contract.
        staged: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                staged = handle.name
                handle.write(dump_contract(current).encode("utf-8"))
            os.replace(staged, path)
        except OSError as exc:
            raise RuleWriteError(exc.errno, exc.strerror, exc.filename) from exc
        finally:
            if staged is not None:
                Path(staged).unlink(missing_ok=True)
    return {
        "contract": str(contract),
        "applied": applied,
        "rejected": len(decisions.rejected("rule")),
    }
