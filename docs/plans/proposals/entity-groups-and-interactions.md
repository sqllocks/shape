# Proposal: coordinated entity groups and interactions in behavior modules

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

This file merges two requests: coordinated groups of entities, and events between entities (a counterparty). They are one proposal because they need the same engine change and the same event schema change (section 1, last paragraph). Interactions with a passive counterparty need no groups, so they are phase 1 and can ship alone.

## 1. The problem

Many processes worth simulating are about several entities acting together, or acting on each other:

- a ring of accounts that moves money A to B to C and back to A, each hop a few hours after the last, with a small fee taken at each hop;
- an attacker who logs on to one host, then uses it to reach the next, across a chain of hosts in a network;
- a staged claims ring: several claimants report accidents within days of each other, all at the same repair shop or clinic;
- ordinary transfers, payments and messages from one customer to another, with a fan-in to a merchant or a fan-out from a payroll account.

Detection rules, graph analytics and fraud models are tested on exactly these shapes. Behavior modules cannot produce any of them:

- **Entities are independent by design.** `docs/plugins/behavior.md:55` says "Entities never interact with each other, which is what makes the run vectorizable, resumable and independent of how the population is split", and the limits repeat it (`behavior.md:313`, `behavior.md:389`).
- **The simulator advances each entity on its own clock.** A round of `run_until` (`plugins/shape-behavior/src/shape_behavior/simulator.py:255`) takes each entity's earliest pending module instance (`simulator.py:263` to `266`) and enters it. Two entities in the same round can be at different virtual times, so one entity cannot wait for, or react to, another.
- **Every draw is keyed by one entity.** `rng.uniform(seed, entity, stream, k)` (`rng.py:41`), and population attributes are drawn per `(seed, entity id, attribute name)` (`population.py:174`, docstring at `population.py:179`). There is no key for a draw shared by several entities, so a group cannot have one start time, one target or one amount.
- **The event schema has no second entity.** `EVENT_SCHEMA` (`events.py:22`) has `entity_id` and a free-text `ref` (used for the onset state of an end event and for a transaction id), but no column for the entity on the other side.
- **Population attributes are independent draws** (`population.py:210` to `223`), so membership of a group cannot be expressed as an attribute either.
- **The table-level simulators do not cover it.** `financial_patterns` fraud bursts (`plugins/shape-simulation/src/shape_simulation/financial_patterns.py:352`) send all transactions of a burst "from one randomly chosen account", with no counterparty and no second account.

What exists and is reused: transaction ids and the `ref` link between a transaction and its refund (`transaction_stream`, `behavior.md:441`), the condition language (`conditions.py`), the extension point for new state types (`extension.py:96`), and checkpoints (`simulator.py:300`).

Why one proposal: a coordinated group is only useful when its members act on each other (each example above is a set of interactions inside a group), and an interaction that changes the other entity's state needs both entities simulated together, which is a group. The event schema change (a counterparty column) is the same for both. The one part that stands alone is an interaction whose counterparty only receives: its state does not change, so it can be named without being simulated together. That is phase 1.

## 2. Proposed design

### a. Counterparty on events (phase 1)

- `EVENT_SCHEMA` gains a nullable `counterparty_id` (int64) after `entity_id`, present only in runs where some module uses it (section 3).
- A domain `event` state may carry `counterparty`:
  - `{"from": "population"}`: an entity id drawn uniformly from the population's id range, never the entity itself;
  - `{"from": "population", "where": <condition>}`: only entities whose initial attributes satisfy the condition (for example `segment == "merchant"`), drawn deterministically from the matching ids;
  - `{"from": "attribute", "attribute": "employer_id"}`: an id held in a numeric attribute (payroll to a fixed employer);
  - optional `"distribution": "zipf"` and `alpha`, so a few counterparties receive most events (popular merchants).
- The counterparty is passive: nothing about its state, attributes or timeline changes. That keeps entities independent, so sharding, resume and vectorization are unchanged. Drawing from the whole population range (not the shard) keeps output independent of how the population is split.

### b. Groups (phase 2)

A `Population` gains `groups`: named kinds of group, each with a count, a size distribution and group-level attributes.

```json
"groups": {
  "ring": {"count": 40, "size": {"kind": "uniform", "low": 3, "high": 8},
           "attributes": {"hop_fee": {"kind": "uniform", "low": 0.01, "high": 0.04},
                          "start_offset": {"kind": "uniform", "low": 0, "high": 300, "unit": "days"}}}
}
```

- Members are extra entities, created in id blocks after the ordinary population (`first_id + size` onward), one block per group in group order. Block boundaries are a pure function of the seed and group id, so any group range gives the same members.
- Group attributes are drawn once per group with the key `(seed, group id, attribute name)` in a reserved stream (like `rng.INIT_MODULE` today). Members read them in conditions and values as `group.<name>`; their own attributes are drawn as today.
- Each member has a `role`: `hub` for the first member and `member` for the others by default, or roles with weights (`{"leader": 1, "mule": 4}`).
- Sharding is by group: an id range must not split a group, and the simulator refuses one that does (naming the group and the range to use).
- `entities()` gains `group_kind`, `group_id` and `group_role` columns (null for ungrouped entities). A new `groups()` table has one row per group: `group_kind`, `group_id`, size, member ids in order, and its attributes.

### c. Group modules and graph shapes (phase 3)

A module with `"scope": "group"` runs once per group on the group's own timeline, which starts at the group's arrival (`start_offset` or the population's `arrival`). Its states are the ordinary ones plus one new state type, `member_action`:

```json
"move": {"type": "member_action", "event": "transfer",
         "shape": "cycle", "gap": {"kind": "exponential", "mean": 6, "unit": "hours"},
         "value_from": "group.amount", "value_factor_per_hop": {"subtract": "group.hop_fee"},
         "transition": {"direct": "rest"}}
```

- `shape` gives the order of the edges between members: `chain` (member 1 to 2 to ... to n), `cycle` (a chain and back to the first), `fan_in` (every member to the hub), `fan_out` (the hub to every member), `pairs` (random pairs without replacement), `all_pairs`. Each edge is one event on the sending member with the receiving member as `counterparty_id`; with `"emit_on": "both"` the receiver also gets an event (`transfer_received`).
- `gap` is the delay between consecutive edges, so members act in sequence on one shared clock. The state is left after the last edge.
- `value_factor_per_hop` changes the value along the path (a fee per hop, or splitting in a fan-out with `"split": true`).
- Members may also run ordinary entity modules (their everyday activity), so a ring account has normal transactions next to the ring's.
- Group draws are keyed by `(seed, group id, module, step, k)`. Group instances and entity instances share the virtual clock. The simulator processes a group's module instances and its members' instances in time order (ties: group modules first, then module order), which is the same rule as today with the group as the unit.

**Ground truth.** A run with groups writes `groups.parquet` (section b) and `group_edges.parquet`: one row per edge with `group_kind`, `group_id`, edge number, from and to entity ids, time, event kind, and the `(entity_id, seq)` of the event row it produced. This is the answer key a detection rule is scored against. `shape behave run` adds both files and their counts to `run.json`.

## 3. Compatibility

- **Event schema.** `EVENT_SCHEMA` is documented as fixed and as API v1 (`behavior.md:35`, `behavior.md:206`). The proposal adds `counterparty_id` only when a module of the run declares a counterparty or a group scope, so every run of version 1 modules keeps today's 14 columns and byte-identical Parquet. The plugin API change is recorded in `docs/plugins/stability.md` as additive.
- **Module format.** `_validate` (`model.py:220`) checks only the keys it knows, so an older reader silently ignores a top-level `scope` and an unknown `counterparty` key on a state. Both therefore need a new module format: `shape-behavior/2`. The calendar-aware behavior proposal also asks for version 2; the lead decides whether both share one bump (open question 1). An unknown state type (`member_action`) is already a hard error in older readers (`model.py:275`).
- **Population document.** `Population.from_dict` refuses unknown keys (`population.py:82`), so a population spec with `groups` already fails loudly on an older reader.
- **Checkpoints.** The checkpoint meta has `"version": 1` (`simulator.py:322`) but `resume` never reads it, and it has no `format` key. A checkpoint of a run with groups adds group arrays, so it is version 2, with `format` added, and `resume` must refuse a newer version naming the release that reads it, as the state and compatibility policy (`docs/specs/STATE_AND_COMPATIBILITY.md`) requires.
- **`run.json`** has no `format` or `version` (`cli.py:175`). It gains new keys here, so it is the point to add both.
- **Determinism.** Version 1 modules and populations without `groups` give byte-identical events and entities before and after the change. Group draws use a reserved stream, so adding groups to a population does not change the draws of its ordinary entities.

## 4. Wanted (ready to paste into the issue)

Phase 1, passive counterparties:
1. A nullable `counterparty_id` (int64) event column, present only in runs where a module uses `counterparty`; the event schema otherwise unchanged.
2. `counterparty` on `event` states with `from` `population` (optional `where` condition on initial attributes, optional `zipf`) or `attribute`; never the entity itself; drawn from the whole population id range, deterministic per seed and independent of sharding.
3. Module format `shape-behavior/2`, required when a module uses any key of this proposal; version 1 modules unchanged and byte-identical in output.
4. The `transaction_stream` primitive gains an optional `counterparty` parameter that passes item 2 through.

Phase 2, groups:
5. `Population.groups`: kinds with `count`, `size` distribution, group `attributes` and member `roles`; member id blocks after the ordinary population, deterministic per seed and group id.
6. `group.<name>` readable in conditions, `value_from` and probability attributes.
7. `entities()` gains `group_kind`, `group_id`, `group_role`; a new `Simulator.groups()` table; `shape behave run` writes `groups.parquet`.
8. A shard (id range) that splits a group is refused with a message naming the group and a valid range.
9. Checkpoint version 2 with a `format` key holding the group arrays; `resume` refuses a newer version; version 1 checkpoints keep loading (frozen fixture).

Phase 3, group modules:
10. Module `"scope": "group"`, run once per group on its timeline, processed in time order with its members' instances.
11. State type `member_action` with `shape` (`chain`, `cycle`, `fan_in`, `fan_out`, `pairs`, `all_pairs`), `gap`, `value_from`, `value_factor_per_hop`, `split`, `emit_on`.
12. `group_edges.parquet` ground truth linking every edge to its event row; counts in `run.json`, which gains `format` and `version`.
13. Example modules: a money-moving ring (`cycle`), lateral movement across hosts (`chain`), a staged claims ring (`fan_in` to a shared provider entity).
14. Documentation in `docs/plugins/behavior.md`: section 1 and the limits rewritten to say what may interact and what stays independent; each new key and state type; the output files.

Acceptance beyond section 13's common items: every edge in `group_edges.parquet` matches exactly one event row and the reverse; edges of a `cycle` group of size n form one cycle of length n; consecutive edges of a group are `gap` apart in time order; the same seed gives byte-identical events, entities, groups and edges for any valid split of the population into group-aligned ranges and across a checkpoint resume; version 1 modules give byte-identical output before and after the change; an older reader rejects a version 2 module.

## 5. Open questions for the lead
1. Whether `shape-behavior/2` is shared with the calendar-aware behavior proposal (one bump, one release) or each takes its own version.
2. Whether a member's ordinary modules may react to the group (for example a guard on `group.started`), which needs group state readable by member instances at the right time, or whether phase 3 keeps member modules independent of the group timeline.
3. Whether `counterparty_id` should be added to the fixed schema for every run instead (simpler for packs, but every existing run's Parquet changes).
