# Behavior models: `sqllocks-shape-behavior`

A domain-agnostic engine for things that *happen to entities over time*: a subscriber trials,
pays, pauses and cancels; a pump runs, degrades, fails and is repaired; a patient is screened,
diagnosed, treated and followed up. You describe the process as a **state machine** in a small
declarative document, run it for a population on a **virtual clock**, and get a stream of
timestamped **events** as an Arrow table. A domain pack turns those events into its tables
(claims, invoices, work orders). Nothing in the engine knows about any one domain.

```
module documents ──► Module ─┐
                             ├─► Simulator (virtual clock, seed) ──► events (Arrow)
Population (attributes) ─────┘                                  └──► entities (Arrow)
```

Status of this page: **API v1, stable for domain packs to build on.** The names below are the
contract; the implementation behind them may get faster. Anything not listed here is private.

```bash
pip install sqllocks-shape-behavior          # numpy and pyarrow only, nothing heavy
shape behave run subscription equipment_maintenance --population 50000 --years 5 --seed 7 -o out/
```

## 1. Concepts

| Term | Meaning |
|---|---|
| **entity** | One simulated thing (member, subscriber, machine). Integer id, arrival time, optional end time, attributes. |
| **module** | One state machine. Several modules run side by side for the same entity and talk to each other through shared attributes and the "active condition / medication" sets. |
| **state** | A node. Entering a state at time *t* applies its effect (emit an event, set an attribute, ...) and chooses the next state. |
| **virtual clock** | Time is an integer count of microseconds since 1970-01-01 (UTC). Nothing reads the wall clock. Delays add to it; the run stops at the `until` time you give. |
| **event** | One row: an entity entered a state that emits something. |

Time moves per entity: an entity's instances of all modules are processed strictly in time
order (ties broken by module order), so a module can read what another module did earlier.
Entities never interact with each other, which is what makes the run vectorizable, resumable
and independent of how the population is split.

## 2. Module document (native format `shape-behavior/1`)

JSON (or a Python `dict`). Minimal example:

```json
{
  "format": "shape-behavior/1",
  "name": "trial_to_paid",
  "initial": "start",
  "attributes": {"plan": {"kind": "categorical", "values": {"basic": 0.7, "pro": 0.3}}},
  "states": {
    "start":  {"type": "initial", "transition": {"direct": "trial"}},
    "trial":  {"type": "delay", "delay": {"kind": "exact", "value": 14, "unit": "days"},
               "transition": {"distributed": [{"p": 0.4, "to": "paid"}, {"p": 0.6, "to": "gone"}]}},
    "paid":   {"type": "event", "event": "became_customer", "transition": {"direct": "end"}},
    "gone":   {"type": "event", "event": "trial_lapsed", "transition": {"direct": "end"}},
    "end":    {"type": "terminal"}
  }
}
```

Top-level keys: `format`, `name`, `initial` (state entered on arrival; default: the only state of
type `initial`), `attributes` (population attributes this module needs, merged across modules,
section 4), `states`, optional `remarks`.

### 2.1 State types

Every state may carry `"remarks"`. All but `terminal` and `death` have a `transition`.

| `type` | Fields | Effect |
|---|---|---|
| `initial` | | Entry point. No event. |
| `terminal` | | The instance stops. No event. |
| `simple` | | Pass-through. No event. |
| `delay` | `delay` | The next state is entered after the delay. No event. |
| `guard` | `condition`, optional `poll` | Waits until the condition holds, then transitions. A condition that depends only on age is waited for exactly; any other condition is re-tested every `poll` (default: the simulator's `poll`, 7 days). No event. |
| `set_attribute` | `attribute`, `value` or `distribution` | Sets an entity attribute. No event. |
| `counter` | `attribute`, `action` (`increment`/`decrement`), `amount` | Adds to a numeric attribute. No event. |
| `encounter` | `codes`, `encounter_class` | Event `kind=encounter`. |
| `encounter_end` | | Event `kind=encounter_end`. |
| `condition_onset` | `codes` (first one is used), `assign_to_attribute` | Event `kind=condition_onset`; the code becomes **active** for the entity. |
| `condition_end` | `condition_onset` (state name) or `codes` or `referenced_by_attribute` | Event `kind=condition_end`; the code stops being active. |
| `medication_order` | `codes`, `reason`, `assign_to_attribute` | Event `kind=medication_order`; active until ended. |
| `medication_end` | `medication_order` (state name) or `codes` or `referenced_by_attribute` | Event `kind=medication_end`. |
| `procedure` | `codes`, `reason` | Event `kind=procedure`. |
| `observation` | `codes`, `unit`, one of `exact`, `range`, `attribute` | Event `kind=observation` with `value`. |
| `death` | | Event `kind=death`; the entity ends now. |
| `event` | `event` (name), `codes`, `value`/`value_from`, `text_from`, `payload` | **Domain event** (section 5): event with `kind=<event>` for packs to turn into rows. |

`codes` is a list of `{"system": "...", "code": "...", "display": "..."}`. Shape ships no
terminology: you supply the codes (and the right to use them).

Durations: `{"kind": "exact", "value": 30, "unit": "days"}`,
`{"kind": "uniform", "low": 1, "high": 3, "unit": "weeks"}`,
`{"kind": "exponential", "mean": 90, "unit": "days"}`,
`{"kind": "gaussian", "mean": 30, "std": 5, "unit": "days"}` (negative draws clip to 0),
`{"kind": "lognormal", "mu": 3.0, "sigma": 0.5, "unit": "days"}`. Units: `seconds`, `minutes`,
`hours`, `days`, `weeks`, `months` (30.4375 days), `years` (365.25 days).

### 2.2 Transitions

```json
{"direct": "next"}
{"distributed": [{"p": 0.3, "to": "a"}, {"p": 0.7, "to": "b"}]}
{"conditional": [{"if": <condition>, "to": "a"}, {"to": "default"}]}
{"complex": [{"if": <condition>, "distributed": [{"p": 0.2, "to": "a"}, {"p": 0.8, "to": "b"}]},
             {"to": "default"}]}
```

Probabilities must sum to 1 (within 1e-6; the GMF importer normalizes and warns). A probability
may be `{"attribute": "risk", "default": 0.1}` to read it from a numeric entity attribute. The
transition is chosen when the state is entered; a `delay` then holds the entity before the chosen
state is entered.

### 2.3 Conditions

`true`, `false`, `{"type": "attribute", "attribute": A, "op": OP, "value": V}` (OP is `==`, `!=`,
`<`, `<=`, `>`, `>=`, `is_nil`, `is_not_nil`), `{"type": "age", "op": OP, "value": 18,
"unit": "years"}`, `{"type": "date", "op": OP, "value": "2024-01-01"}` (the virtual clock),
`{"type": "active_condition", "codes": [...]}`, `{"type": "active_medication", "codes": [...]}`,
`{"type": "and"|"or", "conditions": [...]}`, `{"type": "not", "condition": C}`,
`{"type": "at_least"|"at_most", "minimum"/"maximum": N, "conditions": [...]}`.

## 3. Python API

```python
from shape_behavior import (
    Module, load_module, Population, SimConfig, Simulator, Checkpoint,
    import_gmf, ImportResult, register_state_type, StateHandler,
)
```

All of it imports without loading numpy or pyarrow; they load when a simulation runs.

```python
module = load_module("trial_to_paid.json")            # path, dict or built-in example name
population = Population(size=100_000, start="2020-01-01",
                        attributes={"gender": {"kind": "categorical", "values": {"F": .5, "M": .5}}},
                        age_at_start={"kind": "uniform", "low": 0, "high": 90, "unit": "years"})
sim = Simulator([module], population, SimConfig(seed=7))

year1 = sim.run_until("2021-01-01")     # Events for (start, 2021-01-01]; an Arrow table
year2 = sim.run_until("2022-01-01")     # continues exactly where the clock stopped
people = sim.entities()                 # one row per entity: id, arrival, end, attributes
sim.checkpoint().save("run.ckpt.npz")   # resume later, in another process:
sim = Simulator.resume("run.ckpt.npz", [module])
```

| Name | Signature (v1) |
|---|---|
| `load_module(source, *, strict=False)` | A `Module` from a path, a `dict` or the name of a built-in example. Validates; raises `ModuleError` listing every problem. |
| `Module.to_dict()` / `Module.name` / `Module.digest()` | Canonical document; stable content hash (guards `resume`). |
| `Population(size, *, start, attributes=None, age_at_start=None, arrival=None, lifetime=None, first_id=0)` | Who is simulated (section 4). |
| `SimConfig(seed=0, poll="7 days")` | Seed and the guard polling interval. |
| `Simulator(modules, population, config=None)` | Builds the entity arrays. Cheap. |
| `Simulator.run_until(until) -> pyarrow.Table` | Advances the virtual clock to `until` and returns the events with `time` in `(previous clock, until]`. Calling with an earlier or equal time returns an empty table. |
| `Simulator.entities() -> pyarrow.Table` | Entity table at the current clock. |
| `Simulator.now` | The virtual clock, a `datetime`. |
| `Simulator.checkpoint() -> Checkpoint`, `Checkpoint.save(path)`, `Checkpoint.load(path)`, `Simulator.resume(checkpoint_or_path, modules)` | Resumability. A checkpoint holds arrays only (no pickles); `resume` refuses modules whose digest differs. |
| `import_gmf(source, *, strict=False) -> ImportResult` | Section 6. |
| `register_state_type(name, handler)` | Extension point (section 5). |

**Determinism.** The same modules, population and seed give identical events, whatever the
number of `run_until` calls, whether the run was resumed from a checkpoint, and however the
population is split into id ranges. Every random draw is a pure function of
`(seed, entity id, module, step, draw index)`; nothing depends on iteration order or batch size.
Python's `hash()` and global random state are never used.

## 4. Population

`Population` fields:

* `size`, `first_id` (ids are `first_id .. first_id + size - 1`; run id ranges separately to shard).
* `start`: the virtual-clock origin (a date or datetime, UTC).
* `attributes`: `{name: spec}` with `kind` one of `constant` (`value`), `categorical`
  (`values`: weights), `uniform` (`low`, `high`), `normal` (`mean`, `std`, optional `min`,
  `max`), `bernoulli` (`p`, stored 0/1), `lognormal` (`mu`, `sigma`). Numeric attributes are
  `float64` (missing is NaN); categorical ones are text (missing is null).
* `age_at_start`: a distribution in years, giving each entity a `born` time (builtin attribute
  `age`, derived from the clock). Default: every entity born at `start`.
* `arrival`: when the entity's modules begin: `{"kind": "at_start"}` (default) or
  `{"kind": "uniform", "days": 365}` (spread over the window) or any duration distribution.
* `lifetime`: duration distribution from arrival to the entity's end, or none.

Modules may declare the `attributes` they need; a `Population` attribute of the same name wins.

## 5. Events and the extension point

`run_until` returns an Arrow table with a fixed schema (`shape_behavior.EVENT_SCHEMA`):

| Column | Type | |
|---|---|---|
| `entity_id` | int64 | |
| `seq` | int32 | Per-entity event number, 0-based; `(entity_id, seq)` is unique. |
| `time` | timestamp[us] | When the entity entered the state. |
| `module` | string | |
| `state` | string | State name; `condition_end`/`medication_end` carry the onset state in `ref`. |
| `kind` | string | `encounter`, `condition_onset`, ..., `death`, `entity_end`, or a domain event name. |
| `code`, `system`, `display` | string | From the state's first code, nullable. |
| `ref` | string | Referenced state (ends), nullable. |
| `value` | float64 | Observation value, `value_from` attribute or `value`, nullable. |
| `unit` | string | Nullable. |
| `text` | string | `text_from` attribute, nullable. |
| `payload` | string | The state's static `payload`, as JSON text, nullable. |

Sorted by `(time, entity_id, seq)`.

**Domain events.** A `type: "event"` state emits a row with `kind` = its `event` name and
whatever `codes`, `value`/`value_from`, `text_from`, `payload` it declares. A claims pack writes
modules with events such as `claim_submitted`, then derives claim, line and fill tables from the
event table (typically with joins and the entities table). That is the whole contract between a
behavior module and a domain pack.

**New state types.** `register_state_type(name, handler)` adds a type that modules can use as
`"type": name`. A handler is a `StateHandler`:

```python
class StateHandler(Protocol):
    def validate(self, state: dict[str, Any]) -> list[str]: ...        # problems, empty if fine
    def apply(self, ctx: StateContext) -> Emission | None: ...         # vectorized
```

`StateContext` has `.state` (the state document), `.rows` (indices of the entities entering the
state now), `.time` (their entry times, int64 µs), `.attribute(name)`/`.set_attribute(name,
values)` (numpy arrays over `.rows`), `.uniform(k)` (the deterministic draw *k* for each row),
`.age_years`. `Emission` is a set of equal-length arrays for the event columns above. Handlers
get whole arrays, never single rows, and must be deterministic given `ctx`.

## 6. Importing Generic Module Framework JSON

`import_gmf` reads module files **that you download yourself** from the Generic Module
Framework's open library (or write by hand). Shape does not ship them: their terminology
(SNOMED CT, LOINC) is licensed separately from the modules themselves, so you are responsible for
having the right to use the codes your modules contain.

```python
result = import_gmf("diabetes.json")        # a path, a JSON string or a dict
result.module        # a Module, runnable like any other
result.unsupported   # [Unsupported(state="Insulin_Dose", type="Device", reason="...")]
print(result.report())
```

CLI: `shape behave import-gmf diabetes.json -o diabetes.shape-module.json` converts and prints the
report; `shape behave run` accepts a GMF file directly.

Supported (common subset): states `Initial`, `Terminal`, `Simple`, `Delay`, `Guard`,
`SetAttribute`, `Counter`, `Encounter`, `EncounterEnd`, `ConditionOnset`, `ConditionEnd`,
`MedicationOrder`, `MedicationEnd`, `Procedure`, `Observation`, `Death`; transitions `direct`,
`distributed`, `conditional`, `complex`; conditions `True`, `False`, `Gender`, `Age`, `Date`,
`Attribute`, `Active Condition`, `Active Medication`, `And`, `Or`, `Not`, `At Least`, `At Most`.
Everything else is **reported**, never ignored silently: with `strict=False` (default) an
unsupported state runs as a pass-through, an unsupported condition counts as false and an
unsupported transition ends the module there, and each is listed in `result.unsupported`;
with `strict=True` the first one raises `UnsupportedGmfError`.

Mapping notes: GMF `Gender` reads the entity attribute `gender` (`"M"`/`"F"`), so a population
for GMF modules should define it. A wellness `Encounter` is treated as immediate (reported as a
warning). Distributed probabilities that do not sum to 1 are normalized (warning). GMF
`Observation` and `Procedure` durations are not modelled.

## 7. CLI

```
shape behave run MODULES... --population N --years Y --seed S -o OUT
      [--start 2020-01-01] [--population-spec FILE.json] [--window-years 1]
      [--poll "7 days"] [--strict]
shape behave check MODULES...            # validate; exit 1 on problems
shape behave import-gmf FILE [-o OUT.json] [--strict]
shape behave examples [-o DIR]           # write the built-in example modules
```

`MODULES` are native or GMF JSON files, or the built-in example names `subscription`,
`equipment_maintenance`, `healthcare_screening`. `run` writes `OUT/events/part-NNNN.parquet` (one
file per window, so memory stays bounded), `OUT/entities.parquet` and `OUT/run.json` (seed,
modules and digests, counts, import report). Exit codes: 0 success, 1 invalid module, 2 usage
error.

## 8. Domain packs on the engine

A domain pack (claims, invoices, work orders) ships module documents and turns the event table
into its tables. It uses the engine through the public names of section 3 only:
`load_module` / `Module`, `Population`, `Simulator` and the `EVENT_SCHEMA` columns. Clinical
modules are written on this API in a domain pack; Shape ships no terminology, so a pack supplies
(or asks the user for) the code sets it needs.

## 9. Limits

* Entities do not interact with each other.
* One event per state entry; durations of procedures and encounters are not modelled.
* Active condition/medication sets hold up to 64 distinct codes each.
* Guards on non-age conditions are polled, so an event can occur up to one `poll` late.

## 10. The `shape.behaviors` plugin group

A behavior module is also a plugin of the entry-point group `shape.behaviors` (plugin API v1,
protocol `Behavior`; `docs/plugins/api-v1.md`), so `shape plugins list` shows it, the engine can
load it by name and `python -m shape.plugins.kit DISTRIBUTION` checks it.

| Member | Meaning |
|---|---|
| `name` | The registry key; also the entry-point name. |
| `version` | The module's own version string. |
| `states` | The state names the module uses. |
| `attributes` | The entity attributes it reads or writes. |
| `events` | The event kinds it emits (`kind` column of section 5). |
| `simulate(population, seed, years)` | Runs that many entities for that many years from 2020-01-01 and returns the event table; deterministic per seed. |

`shape_behavior.behaviors.behavior(document)` (or the class `ModuleBehavior`) wraps a module
document into such an object, filling the members from the document; the engine runs a registered
behavior that has a `module` (all of those wrapped this way). `shape behave run NAME` accepts the
name of any registered behavior next to files and example names.

The three built-in examples register this way (`shape.behaviors:subscription`,
`:equipment_maintenance`, `:healthcare_screening`). A third party does the same in its own
distribution; `examples/behavior-plugin` is a complete one (installed from outside the repository
by `tests/plugins/test_plugin_kit_install.py`):

```toml
[project.entry-points."shape.behaviors"]
library_loans = "shape_example_behavior:LibraryLoans"
```

```python
from shape_behavior.behaviors import ModuleBehavior

SHAPE_API = "1.0"

class LibraryLoans(ModuleBehavior):
    def __init__(self) -> None:
        super().__init__(DOCUMENT, version="0.1.0")      # DOCUMENT: a module document
```

`shape.plugins.kit.check_behavior` loads the object, checks the members above, runs a small
population twice per seed, and requires the events to be identical, non-empty, and only of
declared kinds and states (rows of the engine itself, with an empty `state`, are the
`entity_end` events of a population lifetime).
