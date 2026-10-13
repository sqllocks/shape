# Proposal: a declarative mapping transform

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

A data migration moves records from a source system's shape into a target system's shape: tables and columns are renamed, status codes are translated through a lookup table, text becomes a decimal or a date, a full name is split into first and last name, an address is merged into one line, and keys are replaced by the target's keys. Testing a migration needs the same records in both shapes: the source data to load, and the target data the migration should produce, with a record of where each target field came from and of every value the mapping could not translate.

Shape can generate the source shape. It cannot derive the target shape from it:

- **Transforms exist, but none maps columns.** The `shape.transforms` protocol is "tables to tables" (`src/shape/plugins/api/v1.py:223` to `229`: `apply(tables, **options) -> dict[str, pa.Table]`). The built-ins are `mask`, `star` and `cdm` (`pyproject.toml:243` to `246`). `star` builds dimensions and facts with surrogate keys (`docs/TRANSFORMS.md:32`); `cdm` only renames tables to entity names (`src/shape/builtins/dimensional.py:30` to `43`).
- **No rename, translation, cast, split or merge.** Column renames and type changes exist only as defects and chaos mutations that damage data on purpose (`src/shape/scenario/library/defects.py:294`, `src/shape/chaos/categories.py:275`). The `lookup` generation strategy reads a column of another generated table by key (`src/shape/generation/lookup.py:1` to `12`); it builds data, it does not translate codes, and a missing key silently gives null.
- **Re-keying exists only inside other features.** `star` assigns 1-based surrogate keys (`src/shape/dimensional/star.py:3` to `9`); keyed masking gives consistent pseudonymous keys across tables with HMAC-SHA256 (`docs/MASK.md:92`) but writes no map from old key to new key.
- **No field lineage.** `ColumnLineage` (`src/shape/generation/engine.py:473`) records which strategy generated a column, not which source field a target field came from. The OpenLineage plugin emits a schema facet only, with no inputs (`plugins/shape-integrations/src/shape_integrations/openlineage_events.py:44` to `59`).
- **No report of unmapped values.** Nothing in `src` or `plugins` records values a mapping could not translate.
- **The command is closed.** `shape transform` has the fixed subcommands `star` and `cdm` and calls them directly (`src/shape/cli/transform.py:34` to `61`), although the docs say the same transforms are plugins (`docs/TRANSFORMS.md:99`).

## 2. Proposed design

A new built-in transform `map` (entry point `shape.transforms:map`), driven by a mapping document, and a subcommand `shape transform map --map MAP.json SOURCE -o OUT`, where `SOURCE` is, as for `star`, an installed domain or a directory of CSV, Parquet or JSON Lines tables (`docs/TRANSFORMS.md:22`).

```json
{"format": "shape-mapping", "version": 1,
 "lookups": {
   "status": {"path": "lookups/status.csv", "from": "legacy_code", "to": "target_code"},
   "country": {"values": {"UK": "GB", "USA": "US"}, "on_unmapped": "keep"}
 },
 "keys": {
   "customer": {"source": "CUST.CUST_NO", "method": "sequence", "order": "CUST_NO", "start": 100000}
 },
 "tables": {
   "contact": {
     "from": "CUST",
     "where": "REC_TYPE == 'P'",
     "columns": {
       "contact_id":  {"rekey": "customer", "from": "CUST_NO"},
       "first_name":  {"split": "FULL_NAME", "pattern": "^(\\S+)\\s+(.*)$", "group": 1},
       "last_name":   {"split": "FULL_NAME", "pattern": "^(\\S+)\\s+(.*)$", "group": 2},
       "status":      {"lookup": "status", "from": "STAT_CD", "on_unmapped": "default", "default": "UNKNOWN"},
       "country":     {"lookup": "country", "from": "CTRY"},
       "credit_limit": {"from": "CRED_LIM", "cast": "decimal(12,2)", "on_error": "null"},
       "address_line": {"concat": ["ADDR1", "ADDR2", "TOWN"], "sep": ", ", "skip_null": true},
       "source_system": {"constant": "LEGACY"}
     }
   },
   "contact_note": {
     "from": "CUST_NOTE",
     "columns": {"contact_id": {"rekey": "customer", "from": "CUST_NO"}, "body": {"from": "NOTE_TXT"}}
   }
 }}
```

- **Tables.** Each target table names one source (`from`), or several with the same columns (`"from": ["CUST_A", "CUST_B"]`, a union in the listed order). An optional `where` (the condition syntax of the `conditional` strategy) keeps matching rows, so one source can feed several targets. Target column order is the order of `columns`. Source columns not mentioned are dropped, and listed in the lineage document.
- **Column expressions.** `from` (copy and rename), `constant`, `cast` (to an Arrow type or `decimal(p,s)`, `date`, `timestamp`, with a `format` for text dates), `lookup`, `split` (a regular expression and a group, or `sep` and `part`), `concat` (with `sep` and `skip_null`), `template` (`"{ADDR1}, {TOWN}"`), `rekey`, and `coalesce` (the first non-null of several columns). Expressions compose in a fixed order: read, `cast`, then `lookup` or the string operation.
- **Lookups.** From a CSV or Parquet file (`path`, `from`, `to`) or inline `values`. `on_unmapped` is `error` (the default), `null`, `keep` (the source value) or `default`. Matching is exact, with an optional `"case": "insensitive"` and `"trim": true`.
- **Re-keying.** `keys` defines a key space once; every `rekey` that names it uses the same map, so foreign keys follow their parent. Methods: `sequence` (sorted by `order`, from `start`), `hmac` (the keyed masking construction and key handling, `docs/MASK.md:92` and `docs/MASK.md:110`; deterministic for a key, never reversible without it), and `offset` (add a constant). A key value with no row in the key's source table is unmapped and follows `on_unmapped`.
- **Outputs.** The target tables, plus a `_mapping/` folder:
  - `lineage.json` (`format` `shape-mapping-lineage`): for each target column, its source table and columns and the expression; the dropped source columns per table.
  - `unmapped.parquet` and a summary in `report.json`: one row per (target table, column, kind, source value) with its count and the first source row numbers; kinds `lookup`, `cast`, `split`, `rekey`.
  - `keys/<key>.parquet`: the source key to target key map of each key space, which reconciliation can join on (the reconciliation extensions proposal).
- **Determinism.** The transform draws nothing. The same source and map give byte-identical output. The map is checked strictly before any row is read (unknown field, missing table or column, unknown lookup or key), as the star map is (`docs/TRANSFORMS.md:73`).
- **Plugin dispatch.** `shape transform NAME` dispatches to any registered `shape.transforms` plugin that declares a command, so `map` is not a third hardcoded case.

With the OpenLineage plugin installed, `shape lineage emit` can also emit the `columnLineage` facet from `lineage.json` (phase 3).

## 3. Compatibility

- **New formats.** `shape-mapping`, `shape-mapping-lineage` and the report are new persisted formats, each with `format` and an integer `version`, registered in `src/shape/compat.py` (`KINDS`, `compat.py:126`) so that a newer version fails naming the release that reads it, as the state and compatibility policy requires.
- **Nothing existing changes.** `star`, `cdm` and `mask` keep their output byte-identical. Generation is untouched, so generator versions and output for existing seeds do not change.
- **CLI.** `shape transform star` and `shape transform cdm` keep their flags and output; the plugin dispatch (item 8) adds names and removes none.
- **Plugin API.** `map` uses the existing `Transform` protocol; no protocol change.

## 4. Wanted (ready to paste into the issue)

Phase 1, the transform:
1. Mapping document `shape-mapping` version 1 (`lookups`, `keys`, `tables`), validated strictly with every problem reported at once.
2. Table mapping with `from` (one source or a union), `where` and ordered `columns`.
3. Column expressions `from`, `constant`, `cast` (with `format` and `on_error`), `lookup` (with `on_unmapped`, `default`, `case`, `trim`), `split`, `concat`, `template`, `coalesce`.
4. Key spaces with `sequence`, `hmac` and `offset`, shared by every `rekey` that names them; a key map per key space.
5. `lineage.json`, `unmapped.parquet` and `report.json` in `_mapping/`, with `format` and `version`.
6. The built-in transform `map` (entry point `shape.transforms:map`) passing `check_transform` of the conformance kit.
7. `shape transform map --map MAP.json SOURCE -o OUT [--format csv|parquet] [--json]`; exit 0, 1 when an `error` policy was hit (with the report written), 2 for a bad map or input.

Phase 2, CLI and documentation:
8. `shape transform NAME` dispatches to any `shape.transforms` plugin that declares a command; `star` and `cdm` unchanged.
9. Documentation in `docs/TRANSFORMS.md` with a migration example: a generated source dataset, a map, the target dataset, the lineage and the unmapped report.

Phase 3, lineage export:
10. The OpenLineage plugin emits a `columnLineage` facet from `lineage.json`.

Acceptance beyond section 13's common items: every target value is traced by `lineage.json` to the source columns it reads; every value that a lookup, cast, split or rekey could not map appears in `unmapped.parquet` with its count, and none is silently null; foreign keys re-keyed through one key space join to their parent in the target exactly as in the source; the same source and map give byte-identical output; a map with an unknown field, table, column, lookup or key is refused before any output is written.

## 5. Open questions for the lead
1. Whether the inverse direction (generate in the target shape, then map back to the source shape) is wanted, which would need invertible expressions or a second map.
2. Whether `hmac` re-keying should share the masking key file and policy (`docs/MASK.md:110`) or take its own key.
3. Whether the mapping document should be readable from a spreadsheet (a common way mapping specifications are written in migrations), as an importer in the W5-06 family.
