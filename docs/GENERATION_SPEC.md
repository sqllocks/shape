# The generation spec: JSON Schema, stability and edit API

Status: experimental.


A generation spec is the JSON document the engine reads: a model, tables whose columns each
carry a generator (a strategy name plus its keys), relationships, business rules, scale presets
and correlated columns. Editors, form builders and other programs can build and change specs
through the interfaces on this page.

The Python modules on this page (`shape.generation.spec_edit` and `shape.generation.spec_schema`) are
Stable interfaces: the names they export, their signatures and the behaviours tools rely on are
listed in [`docs/API_STABILITY.md`](API_STABILITY.md#stable-python-modules).

## The JSON Schema

`shape/schemas/generation-spec-v1.schema.json` (JSON Schema Draft 2020-12; `$id`
`https://shape-as-code.org/schema/1.0/generation-spec-v1.schema.json`). It declares
`x-shape-format: "generation-spec"` and `x-shape-version: 1`, and a spec declares
`"schema_version": 1`.

- It covers every built-in strategy (`sequence`, `distribution`, `temporal`, `foreign_key`, ...)
  and the keys each one reads, including the nested `params` of the strategies that have one and
  the keys of each `distribution` family. A key a strategy does not read is an error (a typo is
  reported where it is), and a key the strategy needs is `required`.
- It is **built from the code**, not written by hand: the strategy names come from the strategy
  registry, the keys from `shape.generation.spec_keys` and the required keys from
  `shape.generation.schema.STRATEGY_REQUIRED_KEYS`. A test fails if the shipped file differs
  from what `shape.generation.spec_schema.build_schema()` returns. After you change a strategy,
  run `python -m shape.generation.spec_schema` to rewrite the file (`--check` only compares).
- A strategy that is not built in (a plugin's) and a `distribution` family that is not built in
  accept any keys.
- Every object accepts `x-*` keys and `$comment`. They are the extension points of the format:
  the engine ignores them and the edit API keeps them.
- `output_type`, the column properties (`nullable`, `null_rate`, `precision`, `scale`,
  `max_length`) and the structure around the generators are the same as the loader's schema
  (`generation-schema-v1.json`, used by `GenSchema.from_dict`). The loader stays lenient about
  generator keys, so a file with a misspelled key still loads (and `GenSchema.validate()` warns);
  the published schema is the strict one.
- The declaration keys `format`, `version`, `shape_version` and `min_shape_version`, and the
  `migrated_from` and `source_content_id` that `shape migrate` adds, are accepted at the top level,
  so a migrated spec loads and validates.
- `temporal.type` and `faker.max_length` are accepted and marked `deprecated`: spec files made
  by `shape.generation.learn` have always carried them and the strategies ignore them.

```python
from shape.generation.spec_schema import published_schema
schema = published_schema()          # the dict; the file is shape/schemas/generation-spec-v1.schema.json
```

## Stability promise (1.x)

Within 1.x, a spec that is valid today stays valid and means the same:

1. `schema_version` stays `1`. A spec with another version is rejected, never guessed at.
2. A strategy name, a generator key, a `params` key or a `distribution` family is never removed
   or renamed. A deprecated one stays accepted.
3. A key that is optional never becomes required, and a new key is optional with a default that
   gives the old behavior.
4. New strategies, keys, families and `output_type` values may be added (a tool should keep the
   keys it does not know, which is what `SpecDocument` does).
5. The meaning of a key is not changed silently. A fix that changes generated values follows the
   determinism policy in `docs/DETERMINISM.md`.
6. The JSON Schema file is versioned by its name (`-v1`). A change that only adds keeps the file
   name; a breaking change would be a `-v2` file next to it, with a migration.

**Compatibility tests.** `tests/generation/spec_compat/<strategy>.json` is a spec frozen at 1.0
for each built-in strategy. `tests/generation/test_w1_06_spec_compat.py` checks that
each one still validates against the published schema, loads, has no semantic errors and
generates the same table twice. `_keys-1.0.json` is the key table of 1.0; the test fails if a
strategy, a key, a `params` key or a family is missing from it later, or if a key that was optional
became required. Adding a strategy needs a new frozen spec (the test lists strategies without one).

**Generator versions.** An optional top-level `generators` map, `{"<strategy or distribution
name>": <integer >= 1>}`, pins the generator version of each name; a name that is not pinned runs at
its latest version. A spec that pins every name it uses gives the same dataset id in every 1.x
release (see [GENERATION_STABILITY.md](GENERATION_STABILITY.md)). `shape pin SPEC` writes the map,
`shape pin SPEC --check` lists the names that are not pinned, and a pin to a version this Shape does
not have is an error (exit 2). The key is additive under the promise above and `SpecDocument` keeps
it (`doc.generators`, `set_generator_version`, `remove_generator_version`).

**Identifiers.** An optional top-level `"identifiers"`, `"reserved"` (the default) or
`"realistic"`, is the schema's default for the identifier providers (`email`, `company_email`,
`uri`, `phone_number`, `ssn`, and the `faker` package's e-mail, URL, host and phone providers): the
run switch (`identifiers=` of `shape.generate`, `--identifiers`) wins over it, and a column's own
`domains` or `range` key wins over both. Any other value does not load. A schema without it is
written back without it. See [GENERATION_STRATEGIES.md](GENERATION_STRATEGIES.md).

## Load, validate, edit and save

```python
from shape.generation.spec_edit import SpecDocument, SpecError, validate_text

doc = SpecDocument.load("shop.json")            # SpecDocument.loads(text), .from_dict(dict)
for problem in doc.validate():                  # errors and warnings, each with its place
    print(problem)                              # line 14, column 9: /tables/orders/columns/total/generator/lw: ...

doc.set_generator("orders", "total", {"strategy": "normal", "mean": 5.0, "stddev": 1.0})
doc.add_column("orders", "note", "string", {"strategy": "constant", "value": "n"}, nullable=True)
doc.set("/model/seed", 9)                       # any place, by JSON Pointer; get(), remove() too
doc.save("shop.json")                           # atomic: written aside, then renamed (mode kept)

schema = doc.to_schema()                        # the typed GenSchema the engine runs
```

- **Nothing is lost.** The document is the JSON as it was read: unknown keys, `x-*` keys,
  `$comment` and fields a newer 1.x adds stay where they are, and key order is kept.
  A spec that was loaded and not changed is saved byte for byte. JSON has no comments; use
  `$comment` (accepted on every object). `GenSchema.to_dict()` is not a safe way to edit a spec,
  because the typed model keeps only the fields this version reads.
- **Edits** are `get`, `set`, `remove` (JSON Pointer; `~1` is `/` and `~0` is `~`),
  `add_table`, `remove_table`, `add_column`, `remove_column` and `set_generator`. A missing
  table or column is a `KeyError` that names it.
- **Validation** checks the published schema first and, if the spec has the right shape, the
  semantic rules of `GenSchema.validate()` (an FK to a table that is not there, a primary key
  column that does not exist, a rule on an unknown table, ...). A spec is usable when no problem
  has `level == "error"`; `doc.raise_for_errors()` raises a `SpecError` that lists them.
- **Locations.** Each `SpecProblem` has `pointer` (JSON Pointer, e.g.
  `/tables/orders/columns/total/generator/lw`), `path` (the same with dots), `message`, `level`
  and, for a spec loaded from text and not edited since, `line` and `column` (from 1, at the
  key). An unknown generator key says what is wrong with it: that `scale` is a column property,
  or the closest key (`did you mean 'low'?`). A JSON syntax error or a duplicate key is a
  `SpecError` with the line and column. `validate_text(text)` returns the problems of a text
  without raising.

Spec files are JSON. The API does not read or write YAML.
