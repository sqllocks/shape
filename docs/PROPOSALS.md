# Proposals and decision files

Some facts about a dataset cannot be settled by a profile alone: whether a column is a foreign key
to another table, whether it holds personal data, what it means. Shape **proposes** these with the
evidence and a confidence, you **accept, reject or defer** each one, and the decision file keeps
your answer, so the question does not come back after the next re-profile.

```bash
shape profile data/ --dataset -o shop.shape
shape proposals propose shop.shape --data data/ -d decisions.json
shape proposals list -d decisions.json --status pending --min-confidence 0.9
shape proposals decide -d decisions.json "relationship:orders.customer_id->customers.customer_id" \
    accept --actor ana --note "orders belong to customers"
shape generate --from shop.shape --decisions decisions.json -o out/
```

## Commands

`shape proposals propose PROFILE.shape -d DECISIONS.json` finds proposals and merges them into the
decision file (created if missing).

* `--data DIR` or `--data NAME=PATH` (repeatable): the profiled data. It adds value evidence
  (containment for relationships, matching values for PII). Without it Shape proposes from the
  profile alone and is less confident.
* `--kinds relationship,pii,semantic`, `--min-confidence C` (default 0.5).
* `--auto-accept THRESHOLD`: accept undecided proposals at or above the confidence, recorded as
  actor `auto-accept`. **Off unless given**, and it never overrides a decision.

`shape proposals list -d DECISIONS.json [--status pending|accepted|rejected|deferred]
[--kind K] [--min-confidence C] [--json]` lists proposals, most confident first.

`shape proposals decide -d DECISIONS.json PROPOSAL_ID accept|reject|defer [--actor WHO] [--note TEXT]`
records a decision. The actor defaults to `$SHAPE_ACTOR`, then the login name. Deciding again
replaces the earlier decision.

`shape generate --from PROFILE.shape --decisions DECISIONS.json` and
`shape plan PROFILE.shape --decisions DECISIONS.json` apply the decisions (see below). Exit codes
are the usual ones: 2 for a bad or too-new decision file or an unknown proposal.

## Python

```python
import shape
from shape.proposals import DecisionFile, propose, apply_decisions
from shape.generation.fit import fit_schema

profile = shape.load("shop.shape")
decisions = DecisionFile.read("decisions.json")          # or DecisionFile.empty()
decisions.update(propose(profile, "data/"))             # merge; rejected ones are skipped
decisions.decide("pii:customers.email", "rejected", actor="ana", note="test data")
decisions.write("decisions.json")
fit_schema(profile, decisions=decisions)                 # generation keeps accepted relationships
```

`propose_relationships`, `propose_pii` and `propose_semantics` run one kind;
`DecisionFile.list(status=, kind=, min_confidence=)`, `.accepted(kind)` and `.entries()` read it.

## What is proposed

A proposal is `KIND:SUBJECT` (its id), a `claim`, a `confidence` from 0 to 1 and the `evidence`.

**Relationships** (`relationship:orders.customer_id->customers.customer_id`), single-column
candidate foreign keys. The evidence is *name* (the column is named after the parent key, or after
the parent table, singular or plural, with a key suffix), *containment* (the share of the child's
non-null rows whose value exists in the parent; needs the data), *type*, *range* (child minimum and
maximum inside the parent's) and *cardinality* (the parent column is unique, checked with the
same key discovery as `shape key`; the child has no more distinct values than the parent).
Confidence is 0.40 name + 0.35 containment + 0.10 range + 0.15 cardinality; without data the
other three are rescaled and the result is capped at 0.85. A column with no name evidence is
proposed only with the data, with every row contained, and at least 20 distinct values. A column
with under half of its rows contained is not proposed. The profiler's own detected foreign keys are
included, marked `profiler_detected`. Composite keys and one-to-one tables are not inferred.

**PII** (`pii:customers.email`): the column name (`email`, `phone`, `ssn`, `full_name`, ...) and,
with the data, the share of sampled string values that match a detector (email, SSN, phone, IPv4).

**Semantics** (`semantic:customers.city`): what a column means, from its name and the pattern the
profile found (email, uuid).

## The decision file

Text JSON, safe to commit. `format` is `shape-decisions` and `version` an integer (1). Keys are
sorted, proposals and decisions are in id order, times are UTC ISO 8601 (`2026-10-03T12:00:00Z`)
and confidences have four decimals, so unchanged findings change no line and one decision changes
only its own lines. A newer `version` than Shape knows is refused with a message that says so.
The JSON Schema is `src/shape/schemas/decisions-v1.schema.json`.

```json
{
  "format": "shape-decisions",
  "version": 1,
  "proposals": [{"id": "pii:customers.email", "kind": "pii", "subject": "customers.email",
                 "claim": {"pii": "email"}, "confidence": 0.98, "evidence": {"...": "..."},
                 "proposed_at": "2026-10-03T12:00:00Z"}],
  "decisions": [{"proposal": "pii:customers.email", "status": "rejected", "actor": "ana",
                 "at": "2026-10-04T08:30:00Z", "note": "test addresses only"}]
}
```

## How decisions are applied

* **Rejected**: never proposed again, whatever the new evidence. `apply_decisions` also removes
  a rejected relationship the profiler had detected, so generation does not use it.
* **Accepted**: kept. An accepted relationship is added to the profile (relationships, the child
  column's foreign-key flags), so `fit_schema(..., decisions=)` and `shape generate --from
  ... --decisions` generate it as a foreign key. Accepted PII and semantic decisions are read with
  `DecisionFile.accepted("pii")`; they do not change the profile.
* **Deferred**: stays in the file, shown as `deferred`, applied as nothing.
* **Pending** proposals that a later run no longer finds are withdrawn; decided ones stay.
* A re-profile keeps a proposal's first `proposed_at` and updates its evidence and confidence.
* Auto-accept is off by default.
