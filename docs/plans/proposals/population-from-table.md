# Proposal: seeding a behavior population from a table

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

A behavior simulation is most useful when it runs over records that already exist. An insurer has policies with a ZIP code, limits, a class and an effective date, and wants the claims that those policies would produce. A CRM system has accounts with a segment and an owner, and wants their activity. A fleet table has devices with a model and an install date, and wants their telemetry. The records come from a table Shape generated, or from a table loaded from a file.

Behavior modules cannot start from such a table:

- **Attributes are only drawn.** `Population.attributes` (`plugins/shape-behavior/src/shape_behavior/population.py:32`) maps a name to a spec, and `_check_attribute_spec` (`model.py:248`) accepts `categorical`, a text `constant`, or a numeric distribution (`dist.check`). `build_store` (`population.py:174`) fills every attribute from those draws (`population.py:210` to `223`). There is no way to give an entity a value it already has.
- **Ids are a range.** Entity ids are `first_id .. first_id + size - 1` (`population.py:182`) and `int64` (`events.py:24`). A table's key, often text such as `POL-000123`, cannot be an entity id, and there is no mapping from entity id to key.
- **Times are drawn too.** `arrival` and `lifetime` are distributions (`population.py:34`, `population.py:189` to `201`), so a policy cannot arrive on its effective date and end on its expiry date.
- **No input path.** `Population.from_dict` accepts only `size`, `start`, `attributes`, `age_at_start`, `arrival`, `lifetime` and `first_id` (`population.py:82` to `92`). `shape behave run` takes `--population N` and an optional `--population-spec` JSON file (`cli.py:60`) and reads no table. `Behavior.simulate(population, seed, years)` takes a count (`behaviors.py:54`).
- **The workaround is a join by position.** The docs tell a domain pack to derive its tables from events "with joins and the entities table" (`docs/plugins/behavior.md:228`). With drawn attributes, the pack has to join entity `i` to row `i` of its own table, and the attributes the simulation used (for example a risk class that changes the claim rate) are not the ones in that row.

## 2. Proposed design

A `Population` may take its entities from a table instead of a count. The table is an Arrow table in the Python API, and a Parquet or CSV file on the command line.

```json
{"from_table": {"path": "out/policy.parquet",
                "key": "policy_id",
                "attributes": {"zip": "zip_code", "limit": "coverage_limit", "class": "risk_class"},
                "arrival": "effective_date",
                "end": "expiry_date",
                "where": "status == 'active'"},
 "attributes": {"risk": {"kind": "uniform", "low": 0, "high": 1}}}
```

- **One entity per row**, in the table's row order, or sorted by `key` with `"order": "key"` (the default for files, so a reordered file gives the same run).
- **Ids.** An integer key column becomes the entity id directly. Any other key keeps entity ids as today (`first_id` upward, in the chosen order) and adds the key as a text column `key` in `entities()` and, optionally, in the event table (`"key_in_events": true`), so a pack joins on the real key.
- **Attributes.** `attributes` maps an attribute name to a column. Numeric columns become numeric attributes (missing is NaN), text and dictionary columns become text attributes (missing is null), dates and timestamps become numeric microseconds. Other types are an error naming the column. Drawn attributes in `Population.attributes` still work beside them; a name given both ways is an error.
- **Times.** `arrival` and `end` name date or timestamp columns. A missing arrival falls back to `start`; a missing end means no end. `age_at_start` may name a column of birth dates with `"born": "birth_date"`.
- **`where`** keeps only matching rows, with the condition syntax of the `conditional` generation strategy (`docs/GENERATION_STRATEGIES.md:294`).
- **Determinism.** Draws stay keyed by `(seed, entity id, ...)` (`rng.py:41`), so the same table and seed give the same events. A run records the table's row count and a content hash in `run.json` and in checkpoints, and `resume` refuses a different table, as it refuses different modules today (`simulator.py:344`).
- **Sharding.** `first_row` and `rows` select a slice of the (ordered) table, so a large table is simulated in parts that give the same events as one run.

The CLI gains `--population-table FILE` with the same keys in `--population-spec`. A generated dataset can be named by its directory and table (`--population-table out/:policy`). Reading a table from a Shape source plugin (a database or a lakehouse) is out of scope for phase 1; the lead may add it (open question 2).

This composes with the other behavior proposals: an entity's attributes from its row can drive a dated shock scoped to matching entities (a storm over some ZIP codes), and group membership can come from a column (the entity groups proposal).

## 3. Compatibility

- **Population document.** `Population.from_dict` refuses unknown keys (`population.py:82`), so a spec with `from_table` fails loudly on an older reader. No format change is needed for the population document itself, but the state and compatibility policy (`docs/specs/STATE_AND_COMPATIBILITY.md`) asks every persisted file to declare `format` and `version`; the population spec has neither today, and this is the change that should add them (open question 1).
- **Module format.** Modules do not change. A module that reads an attribute only a table provides runs against any population that has it.
- **Checkpoints.** The checkpoint meta stores `population.to_dict()` (`simulator.py:326`) and has `"version": 1` (`simulator.py:322`), which `resume` never checks. A population from a table adds the table hash and slice, so checkpoints of such runs are version 2, and `resume` refuses a version it does not know.
- **Event schema.** Unchanged unless `key_in_events` is set, which adds a nullable `key` text column at the end; runs without it keep today's 14 columns.
- **Determinism.** A population without `from_table` gives byte-identical events and entities before and after the change.

## 4. Wanted (ready to paste into the issue)

1. `Population.from_table` (Python: an Arrow table, or a path to Parquet or CSV) with `key`, `attributes` (attribute name to column), `arrival`, `end`, `born`, `where`, `order` (`as_is` or `key`), `first_row`, `rows`, `key_in_events`.
2. Integer keys become entity ids; other keys keep generated ids and add a `key` column to `entities()` (and, with `key_in_events`, to events).
3. Type mapping for attribute columns as in section 2, with an error naming the column for an unsupported type, a missing column, a duplicate key, or a name given both as a column and as a distribution.
4. A table hash and slice in `run.json` and in a version 2 checkpoint; `resume` refuses a different table and a newer checkpoint version; version 1 checkpoints keep loading (frozen fixture).
5. `shape behave run --population-table FILE` (and `DIR:TABLE` for a generated dataset); `--population N` is then an error.
6. A `format` and integer `version` for the population spec document, with older unversioned specs still accepted.
7. Documentation in `docs/plugins/behavior.md` section 4, with a worked example: a generated policy table, a claims module whose rate depends on the policy's class, and the claim events joined back on `policy_id`.

Acceptance beyond section 13's common items: every entity's attributes equal its row's values; arrival and end equal the row's dates; the same table and seed give byte-identical events whatever the slicing into `first_row` and `rows` ranges and across a checkpoint resume; a reordered file gives identical events with `order: key`; a population without `from_table` gives byte-identical output before and after the change.

## 5. Open questions for the lead
1. Whether the population spec should get `format` and `version` in this package or in a separate W1-01 follow-up.
2. Whether reading the table through a Shape source plugin (so a database table can seed a simulation without an export) belongs in phase 1.
3. Whether a table attribute may change during the run from a second, dated table (for example a policy's limit changing at renewal), or whether that stays a module's job.
