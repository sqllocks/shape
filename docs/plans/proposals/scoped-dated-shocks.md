# Proposal: dated shocks scoped to matching entities

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

Some events hit part of a population on a date. A storm crosses a set of ZIP codes and claims from policies there surge for weeks. A data breach at one card processor raises fraud reports for the cards it handled. An outage in one region floods its support queue. A recall affects one product line. Each shock has a date, a scope (the entities that match a condition), and a profile in time: a ramp up, a peak and a decay. Testing a catastrophe dashboard, a capacity plan or a detection rule needs data that has this shape, and a ground truth of which records the shock caused.

Nothing in Shape expresses it:

- **Calendar effects depend on the date only.** `docs/GENERATION_CALENDARS.md:24` to `26`: "Every effect gives one multiplicative factor per day ... A factor depends on the date only, so any sub-range of a calendar is the slice of a longer one." The stable plugin protocol has `name` and `lift(start, end)` (`src/shape/plugins/api/v1.py:169` to `176`): one factor per day, with no row or entity argument.
- **The time profile already exists.** A calendar `Event` (`src/shape/builtins/calendars/effects.py:39`, `GENERATION_CALENDARS.md:28`) has a `date`, a `lift`, `ramp_up_days`, `decay_days`, and `linear` or `exponential` ramp and decay. What it lacks is a scope.
- **Behavior modules have scope but no dated rate change.** Conditions can test an entity's attributes (`plugins/shape-behavior/src/shape_behavior/conditions.py:175`), so a module can tell "in the storm area" from "not". But delays are fixed distributions (`dist.py:12`) that do not change with the date, and the calendar-aware behavior proposal (`calendar-aware-behavior.md`, item 2b) reshapes where a delay lands by thinning: it moves events in time but does not add any. A storm adds claims.
- **Generated tables draw the date and the parent independently.** A claim's policy comes from `foreign_key` and its date from `temporal`, each row addressed from its own stream (`docs/GENERATION_STRATEGIES.md:38`), so the date cannot depend on the policy's ZIP code.

**Where it belongs, with evidence.**
- **Not in calendars.** Adding a scope would break the documented property that a factor depends on the date only (`GENERATION_CALENDARS.md:25`), which the calendar slicing and the profile read-back (`holiday_lifts`, `GENERATION_CALENDARS.md:84`) rely on, and it would change the stable `shape.calendars` protocol. The shock reuses the calendar `Event` spec for its time profile only.
- **In behavior modules first.** A shock changes how many events happen to whom, which is a rate per entity over time: exactly what the simulator models, and the only place where the shock can add events and record which ones it caused.
- **In the `temporal` strategy as a smaller second step.** For tables generated without a simulation, a row-addressed version can move a share of the matching rows into the shock window. It cannot add rows (the row count is the table's), so it is phase 2 and documented as such.

## 2. Proposed design

### a. Behavior shocks (phase 1)

A run (Python `SimConfig`, or a `shocks` file for `shape behave run --shocks FILE`) declares shocks. Each has a time profile, a scope and an effect:

```json
{"format": "shape-behavior-shocks", "version": 1,
 "shocks": [
  {"name": "storm_2024_09",
   "profile": {"date": "2024-09-12", "lift": 8.0, "ramp_up_days": 1, "decay_days": 30, "decay": "exponential"},
   "where": {"type": "attribute", "attribute": "zip", "op": "in", "value": ["33101", "33109", "33125"]},
   "effect": {"kind": "rate", "states": ["wait_for_claim"]}},
  {"name": "storm_2024_09_direct",
   "profile": {"date": "2024-09-12", "decay_days": 10, "decay": "exponential"},
   "where": {"type": "attribute", "attribute": "zip", "op": "in", "value": ["33101", "33109", "33125"]},
   "effect": {"kind": "trigger", "module": "claims", "state": "claim_reported", "probability": 0.25}}
 ]}
```

- **`profile`** is a calendar `Event` spec (`date` or `dates`, `lift`, `ramp_up_days`, `decay_days`, `ramp`, `decay`), so its factor on a day is computed by the same code as a calendar's.
- **`where`** is a condition of the module condition language, tested on the entity's attributes when the shock starts (its first ramp day). An `in` operator (a list of values) is added to the attribute condition, since an area is a set of codes.
- **`effect.kind: "rate"`**: on the listed delay states of matching entities, the event rate is multiplied by the day's factor. The delay must be `exponential` (a rate) or an explicit `rate` kind; any other kind is a validation error, because "more often" is not defined for a fixed delay. Sampling is by thinning against the profile's maximum factor, deterministic per `(seed, entity id, module, step)`.
- **`effect.kind: "trigger"`**: each matching entity enters `state` of `module` once, with `probability`, at a time drawn from the profile's shape (its weights over the window). The triggered instance then follows the module's transitions as usual. This is the direct hit: a claim for storm damage, not a change in the everyday claim rate.
- **Ground truth.** The run writes `shocks.parquet`: one row per event the shock caused, with the shock name, `entity_id`, `seq` and the effect. For `rate`, an accepted event at factor `f` is attributed to the shock with probability `(f - 1) / f`, drawn deterministically, so the expected count of attributed events equals the excess over the baseline. `run.json` lists the shocks with their matched-entity counts.
- **Composes with** the population-from-table proposal (the ZIP code comes from each policy's row) and the entity groups proposal (a shock may scope a group kind).

### b. Generation shocks (phase 2)

`temporal` gains `shocks`: `[{"name", "profile", "where": "zip IN ('33101', '33109')", "share": 0.4}]`. For a row whose same-row columns match `where` (the syntax of `conditional`, with an `IN` list added), the date is drawn from the shock profile with probability `share`, and from the column's ordinary profile otherwise. This is row addressed (the condition reads the same row; the choice and the draw use their own labels), so it keeps rule 1. It reshapes when matching rows happen; it does not change how many there are. A matching ZIP column comes from `derived` (`copy` through the foreign key) before `temporal`. With `"flag_column": "shock_name"`, a column records which shock moved the row, as ground truth.

## 3. Compatibility

- **Behavior.** Shocks are a run input, not a module key, so modules do not change format. The shocks file is a new persisted format with `format` and an integer `version`, following the state and compatibility policy (`docs/specs/STATE_AND_COMPATIBILITY.md`). The `in` operator is a new value of `op`; an older reader already rejects an unknown `op` with a validation error (`conditions.py:47`), so a module that uses it fails loudly there and needs no format change. A run without shocks gives byte-identical events.
- **Checkpoints.** A run with shocks must record their digest in the checkpoint meta, so `resume` refuses different shocks, as it refuses different modules today (`simulator.py:344`). That is a new checkpoint version.
- **Generation.** `shocks` on `temporal` is a new key in a closed schema (`src/shape/schemas/generation-spec-v1.schema.json:2719`), so an older reader refuses it. Specs without it are byte-identical.
- **Calendars.** Unchanged. The protocol and every calendar's factors stay as they are.

## 4. Wanted (ready to paste into the issue)

Phase 1, behavior:
1. A shocks document (`format` `shape-behavior-shocks`, integer `version`) with `name`, `profile` (a calendar `Event` spec), `where` (a module condition) and `effect` (`rate` with `states`, or `trigger` with `module`, `state`, `probability`); `SimConfig.shocks` in Python and `shape behave run --shocks FILE`.
2. The `in` operator for attribute conditions.
3. `rate` effects by thinning on exponential (or `rate`) delays of matching entities; a validation error for other delay kinds.
4. `trigger` effects: one entry per matching entity with the given probability, timed by the profile.
5. `shocks.parquet` ground truth with deterministic attribution, and the shocks with matched counts in `run.json`.
6. Shock digests in a new checkpoint version; `resume` refuses different shocks and a newer version.
7. An example: a claims module and a storm shock over a set of ZIP codes, in `docs/plugins/behavior.md`.

Phase 2, generation:
8. `temporal` `shocks` (`name`, `profile`, `where` with `IN`, `share`) and `flag_column`, row addressed.
9. Documentation in `docs/GENERATION_STRATEGIES.md` (`temporal`) and a note in `docs/GENERATION_CALENDARS.md` that scoped shocks live outside calendars, and why.

Acceptance beyond section 13's common items: matching entities' daily event counts follow `baseline * factor(day)` within a stated tolerance over a large population, and non-matching entities' counts are unchanged; attributed events in `shocks.parquet` are within tolerance of the excess over the baseline; no shock effect occurs outside the profile's window; the same seed gives byte-identical events and ground truth across a checkpoint resume and any population split; runs without shocks and specs without `shocks` give byte-identical output before and after the change.

## 5. Open questions for the lead
1. Whether `where` should be re-tested during the shock (an entity that moves into the area) or only at its start, as proposed.
2. Whether phase 2 should also skew `foreign_key` toward matching parents inside the window (adding rows to the area at the expense of others), which needs a joint draw of date and parent.
