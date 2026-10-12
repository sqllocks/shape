# Proposal: polymorphic references

Status: proposed 2026-10-12 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

A polymorphic reference is a pair of columns: a **type column** that names the parent table, and an **id column** holding a key of that table. One row of `task` points at an account, the next at an opportunity:

| task_id | regarding_type | regarding_id |
|---|---|---|
| 1 | account | 1042 |
| 2 | opportunity | 77 |

The pattern is common in data Shape profiles and generates:
- Dataverse lookups that can target several tables ("regarding", "customer", "owner");
- Salesforce activities, whose references can point at a person or at a record;
- Rails polymorphic associations (`commentable_type`, `commentable_id`) and Django generic foreign keys (`content_type`, `object_id`);
- comments, attachments, notes, tags and audit logs in most applications;
- FHIR references that allow several resource types (an Observation's subject can be a Patient, a Group or a Device).

Shape cannot express it today:
- **Generation.** `foreign_key` (`src/shape/builtins/strategies/keys.py:78`) takes one `ref`. `conditional` branches allow only `fixed` and `lookup`. Table order comes from `Table.fk_dependencies` (`src/shape/generation/schema.py:166`), which counts only `foreign_key` columns, so the parents of any other strategy are not guaranteed to be generated first.
- **Schema.** `Relationship` (`schema.py:177`) has one `parent`; its JSON Schema is closed (`additionalProperties: false`).
- **Checks.** The `referential_integrity` gate (`src/shape/quality/gates.py:168`) and the row-level `_references` (`src/shape/quality/rowlevel.py:165`) test every child row against one parent, so a polymorphic column would report most rows as orphans.
- **Profiling.** `_detect_fks` (`src/shape/profile/reference/table.py:244`) considers only columns named `<table>_id` and maps one child column to one parent, so `profile` then `generate --from` loses the relationship without a warning.
- **Proposals.** A relationship claim (`src/shape/proposals/relationships.py:262`) maps one column to one parent.
- **Chaos.** `orphan_keys` already takes its orphan base above every candidate parent (`src/shape/chaos/groundtruth.py:472`), so it needs only to know the column is a reference.

The workaround, one nullable key column per target table, generates a different shape from the real one, which defeats Shape's main promise.

## 2. Proposed design

**Discriminated relationships.** A relationship gains an optional `discriminator`: `{"column": "regarding_type", "value": "account"}`. A polymorphic reference is several relationships with the same child column, one per target, each with its own discriminator. Everything that iterates relationships (the gate, row-level checks, fit, chaos) then applies a relationship only to the child rows whose type column equals its `value`. A relationship without `discriminator` behaves exactly as today.

**Generation strategy `polymorphic_foreign_key`** on the id column:

```json
{"name": "regarding_id", "type": "integer",
 "generator": {"strategy": "polymorphic_foreign_key", "type_column": "regarding_type",
               "targets": {"account": {"ref": "account.account_id"},
                           "opportunity": {"ref": "opportunity.opportunity_id", "distribution": "zipf"}}}}
```

- The type column is generated first by any existing strategy (`weighted_enum` gives the mix; `conditional_table` makes it depend on another column).
- For each row, the strategy draws from the parent the row's type names, using that target's options. Each target accepts the same options as `foreign_key` (`distribution`, `alpha`, `max_per_parent`, `fan_out`, `constrained_by`, `sample_rate`, `filter`) and reuses its code, so draws stay row-addressed and deterministic.
- A type value with no target, or a null type: null when the column is nullable, else a `StrategyError` naming the value.
- Every target table is a dependency of the child for ordering and cycle detection.
- Loading a schema that has this strategy derives the discriminated relationships when the schema does not declare them, so the gate checks generated data without extra configuration.

Single-column keys only. Composite polymorphic keys and many-to-many are out of scope.

## 3. Compatibility

- A schema with `discriminator` is refused by older readers (the relationship schema is closed): a loud failure, which is the safe direction. The generation schema's version handling follows the state and compatibility policy (W1-01).
- Profiles: a discriminated relationship in `DatasetProfile.relationships` is a format change and needs a compatibility test. Profiles without one are byte-identical to today's.
- Decision files: a relationship claim with a discriminator is a new claim shape; existing decision files keep working.

## 4. Wanted (ready to paste into the issue)

Phase 1, generation and checks:
1. `Relationship` gains an optional `discriminator` (`column`, `value`) in the dataclass, the JSON Schema and validation: the column must exist in the child and its relationships must share one child column. Relationships with the same child column and no discriminator are an error.
2. A new built-in strategy `polymorphic_foreign_key` (`type_column`, `targets`), registered in `STRATEGY_KEYS`, `STRATEGY_REQUIRED_KEYS` and the published generation-spec JSON Schema, reusing `foreign_key`'s parent pool and draw code per target.
3. Table ordering and cycle detection count every target of a `polymorphic_foreign_key` column as a dependency.
4. A schema whose strategy has no declared discriminated relationships gets them derived on load.
5. The `referential_integrity` gate and row-level `_references` apply a discriminated relationship only to rows whose type column equals its value; rows whose type matches no relationship are reported under a distinct outcome kind (`unknown_type`).
6. Chaos `orphan_keys` treats a discriminated child column as a reference and records the row's type in the ground-truth log.
7. Documentation in `docs/GENERATION_STRATEGIES.md` and the gate documentation, with a worked example; a scenario in the scenario library with an answer key.

Phase 2, profiling (separate package, after phase 1):
8. Detection: a candidate is a text column with few distinct values whose values match table names (case-insensitive; an optional mapping in `shape.yml` for values that differ, such as Rails class names), paired with a column whose values, grouped by that type value, are contained in each named table's primary key at the existing 0.9 threshold.
9. Detected pairs are written as discriminated relationships in the profile and fit to `polymorphic_foreign_key`, so `profile` then `generate --from` round-trips them.
10. Relationship proposals carry the discriminator, so a person can accept or reject a detected polymorphic reference.

Acceptance beyond section 13's common items: generated data passes the gate with zero orphans for every target at every scale preset; the same seed gives byte-identical output; a profile of generated data detects the same discriminated relationships (phase 2).

## 5. Open questions for the lead
1. Whether `discriminator` should allow a list of values (several type spellings for one table) in phase 1, or only through the phase 2 mapping.
2. Whether a contract rule for referential integrity should exist at all (today only the gate checks it), which would also cover discriminated relationships.
