# AUD-design — audit of design, proposals, spec and model

Branch: `lane/AUD-design` (from `origin/build/main-plan`). Area: `src/shape/design`,
`src/shape/proposals`, `src/shape/spec`, `src/shape/model`, `src/shape/schemas` and their tests,
`docs/DESIGN.md`, `docs/PROPOSALS.md`, `docs/specs/`.

No gate, tolerance or D-xx/T-xx decision was changed; no test was skipped, xfailed or had its
expectation changed. None of the files in this area feed an equivalence verifier.

## Phase 1 — baseline

`pytest tests/design tests/proposals tests/spec tests/model --cov=shape.design --cov=shape.proposals
--cov=shape.spec --cov=shape.model`: 230 passed, 92% total. Lowest: `spec/io.py` 42%,
`spec/view.py` 58%, `spec/migrate.py` 65%, `proposals/_data.py` 84%.

## Findings

Severity: critical / high / medium / low. "Repro" is Python run from the repo root.

1. **high — a contract that declares an unknown mandatory capability is accepted.**
   `src/shape/spec/model.py:51` (`ShapeContract.from_dict`). `docs/specs/SHAPE_1_0.md` rule 1: a
   conforming implementation MUST reject unknown mandatory capabilities, and the Shape-as-Code v1
   document is defined by `shape-v1.schema.json`. `from_dict` ignores
   `mandatory_capabilities` and never checks the schema, so it also accepts `name: 123`,
   `version: true`, `version: 1.9` (truncated to 1), `version: 2` and `metadata: [1]`.
   `shape validate` reports such a file `valid: true`.
   Repro: `ShapeContract.from_dict({"name": "x", "fields": [], "mandatory_capabilities":
   ["future/9"]})`. Expected: `ValueError` naming `future/9`. Actual: a contract.
2. **high — star/snowflake: tables whose names collide silently replace each other.**
   `src/shape/design/engine.py:309-421` (`_dimensional` keeps tables in a dict keyed by name, so
   the final `_check_unique` never sees a duplicate). Cases: an entity called `date` (or `Date`)
   used as a dimension next to a fact with `dates` (its `dim_date` is replaced by the date
   dimension); two entities whose names give the same snake-case name (`Customer`, `customer`);
   two facts `Sales` and `sales`; an outrigger `dim_<entity>_<level>` and an entity named
   `<entity>_<level>`. The foreign keys then point at the wrong table and lint reports nothing.
   3NF already raises for the same collision. Repro: in the findings script, entity `date` with
   attributes `date_id`, `d` and a fact with `dimensions: [{entity: date, via: date_id}]` and
   `dates: [when]` → the result has one `dim_date`, the date dimension. Expected: `DesignError`
   naming both sources (lint G001). Actual: the entity's table is lost.
3. **medium — 3NF drops a self-referencing foreign key.** `src/shape/design/engine.py:136`.
   `emp.mgr` with `references: emp` gives table `emp` with no foreign key; `docs/DESIGN.md`
   promises a foreign key "from each `references` attribute to the key of the entity it names".
   Expected: `FOREIGN KEY (mgr) REFERENCES emp (id)`. Actual: none.
4. **medium — decimal scale larger than precision is accepted, giving invalid DDL.**
   `src/shape/design/model.py:187` (`_check`) and `src/shape/design/from_data.py:396-403`.
   `{"type": "decimal", "precision": 5, "scale": 9}` emits `DECIMAL(5,9)`; `design_from_rows` on
   `Decimal("1E-50")` emits `DECIMAL(38,50)`. Every dialect rejects scale > precision. Expected: a
   `DesignError` for a declared input; from data, a precision that holds the scale. Actual:
   DDL that no database accepts.
5. **medium — repeated names inside a key, a dependency or a hierarchy are accepted.**
   `src/shape/design/model.py:195-218`. `keys: [["id", "id"]]` emits `PRIMARY KEY ([id], [id])`
   (invalid); hierarchy `levels: ["a", "a"]` produces an outrigger of a level that is its own
   parent. Expected: `DesignError` "repeats attribute 'id'". Actual: accepted.
6. **medium — `DecisionFile.update` and `decide` write files that `DecisionFile.read` rejects.**
   `src/shape/proposals/model.py:344-439`. A `Proposal` whose `id` is not `KIND:SUBJECT`, whose
   kind is unknown, or whose claim or evidence is not an object, and a decision with
   `note=None`, are stored; `dumps()` then gives a file that `loads()` refuses, so the
   committed decision file is unreadable. Repro: `df.update([Proposal("x", "relationship",
   "a.b", {}, 0.9, {})])` then `DecisionFile.loads(df.dumps())`. Expected: the bad proposal is
   refused by `update`. Actual: `DecisionError` only on the next read.
7. **medium — `apply_decisions` with a `.shape` path fails with a wrong message.**
   `src/shape/proposals/apply.py:183-194`. `dataset_of` accepts a path, but `apply_decisions`
   then treats the path string as the profile: `"tables" not in "…/x.shape"` is a substring
   test, so it raises "needs a multi-table profile; this one has a single table" for a
   multi-table profile, and `AttributeError: 'str' object has no attribute 'get'` when the path
   contains `tables`. Expected: the profile is loaded and the decisions applied. Actual: error.
8. **low — empty names are accepted in a design input.** `shape/schemas/design-input-v1.json`.
   An entity or attribute named `""` emits `CREATE TABLE []` / `[]` columns. Expected: refused
   with the path of the empty name.
9. **low — a measure's `not_additive_over` is not checked.** `src/shape/design/model.py:224`.
   Names that are no dimension, date or attribute of the fact are accepted silently.
10. **low — impossible times pass as UTC stamps.** `src/shape/proposals/model.py:131-141` and
    the readers at lines 249 and 272: `stamp("2026-13-45T99:99:99Z")` is returned as is, and a
    decision file with that time is read. Expected: `DecisionError`.
11. **low — `design_from_rows` on a row that is not a mapping raises `AttributeError`.**
    `src/shape/design/from_data.py:378-385`. Expected: `DesignError` naming the row. Also, a
    column mixing `int` and `Decimal` is typed `string` instead of `decimal`.
12. **low — `check_capabilities` treats a string as a list of characters.**
    `src/shape/spec/capabilities.py:13`. `{"mandatory_capabilities": "core/1"}` reports unknown
    capabilities `/`, `1`, `c`, ...; `None` raises `TypeError`. Expected: a `ValueError` that
    says the field must be a list of strings.
13. **low — contract errors do not say what is wrong.** `src/shape/spec/model.py:33-59`:
    `KeyError: 'name'`, `duplicate field` (which one?), `TypeError: ... unexpected keyword
    argument 'bogus'`. Fixed together with 1 (the schema check names the path).
14. **low — `migrate_capture_v1` can produce two columns with the same name.**
    `src/shape/spec/migrate.py:154`: keys `1` and `"1"` both become column `"1"`. Expected:
    `ModelError`. Only reachable from a Python dict (JSON keys are strings).
15. **low (not fixed, recorded) — proposal ids are ambiguous for names that contain a dot.**
    `pii:a.b.c` is both table `a.b`, column `c` and table `a`, column `b.c`. Changing the id
    format would change the persisted decision-file format; left for the lead.
16. **low (not fixed, recorded) — foreign-key constraint names are not length-checked.**
    `src/shape/design/ddl.py:537`: `FK_<table>_<columns>` can pass 63 characters (PostgreSQL
    truncates, so two long names can clash); lint N002 checks table and column names only.
17. **improvement — untested paths.** `spec/io.py` (YAML), `spec/view.py`, `spec/migrate.py`
    (engine v1, relationships, classifications), `model/core.py` (`join_sensitivity`).

## Issues, fixes and commits

Filled in as the fixes land (below).
