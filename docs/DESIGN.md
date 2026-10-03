# Schema design

Designing a relational or a dimensional schema from a description of the data is mechanical once
the facts are written down. `shape design` does that part: it reads a **design input** (entities,
attributes, keys, functional dependencies, hierarchies, history needs, and facts with a declared
grain and measures), lints it, and writes DDL for a **3NF**, **star** or **snowflake** schema. The
same input always gives the same bytes.

```bash
shape design retail.design.json --mode star --dialect postgres -o retail.sql --json retail.tables.json
shape design retail.design.json --mode snowflake --lint        # the lint report only (JSON)
shape design orders.csv --from-data --name orders -o orders.design.json
```

Exit codes: 0 ok, 1 a lint error (or a warning with `--strict`), 2 bad input. DDL is written to
standard output when `-o` is not given; lint findings go to standard error.

## The design input

A JSON document with `"format": "shape-design"` and an integer `"version"` (1). The JSON Schema is
`shape/schemas/design-input-v1.json` (`shape.design.design_input_schema()`). A version newer than
this Shape supports is refused with an error that says so.

| Field | Meaning |
|---|---|
| `entities[]` | `name`, `attributes[]` (`name`, `type`, `nullable`, `max_length`, `precision`, `scale`, `references`), `keys` (lists of attribute names), `dependencies[]` (`determinant` and `dependent` lists), `history` |
| `entities[].attributes[].type` | `integer`, `string`, `decimal`, `timestamp`, `boolean`, `uuid`, `float`, `date`, `time`, `binary` |
| `entities[].attributes[].references` | the entity this attribute is a foreign key to (its one-column key) |
| `entities[].history` | slowly changing dimension types: `default` and per-`attributes`; 0 retain, 1 overwrite (the default), 2 new row, 3 previous-value column |
| `hierarchies[]` | `name`, `entity`, `levels` from the finest to the coarsest; each level determines the next |
| `facts[]` | `name`, `source` (the entity the fact is measured on), `grain` (attributes of the source that identify one fact row), `measures[]`, `dimensions[]`, `dates`, `degenerate`, `junk`, `many_to_many` |
| `facts[].measures[]` | `name`, `attribute`, `additivity` (`additive`, `semi_additive`, `non_additive`), `not_additive_over` |
| `facts[].dimensions[]` | `entity`, `via` (the source attribute that holds the key), optional `role` |

A name may appear once in a key, a dependency side, a hierarchy or a fact's attribute lists, and a
decimal's `scale` may not exceed its `precision`; either is refused with the path of the problem.

Declared keys count as dependencies (`key -> every other attribute`). An entity with no key gets
the first candidate key found from its dependencies.

## 3NF (`--mode 3nf`)

Per entity: Bernstein's synthesis over a **minimal cover** of its dependencies. One relation per
distinct determinant (the determinant plus everything it determines), relations contained in
another are dropped, and a relation holding a candidate key of the entity is added when none has
one. The result is **lossless-join** and **dependency-preserving**, and each relation is in 3NF.
A relation that holds the entity's key is named after the entity; the others after their key
(`customer_city`). Foreign keys: from each `references` attribute to the key of the entity it
names, and between the relations of one entity wherever one holds another's key.

These guarantees are checked by tests, not asserted: the chase (`shape.design.fd.is_lossless`),
the dependency-preservation test (`preserves_dependencies`) and a 3NF test on projected
dependencies run on textbook examples and on random inputs (hypothesis).

Limits: the candidate-key search and the 3NF test are exponential in the number of attributes they
search (22 for keys, 14 for projecting dependencies). Past that the engine stops with an error that
asks for declared keys.

## Star and snowflake (`--mode star`, `--mode snowflake`)

Kimball's rules, per declared fact:

- **Atomic grain.** The fact's grain is declared, and it must determine every attribute the fact
  uses (checked with the source entity's dependencies). Each grain attribute must reach a column of
  the fact: through a dimension, a date, or a degenerate dimension. The fact's primary key is those
  columns.
- **Surrogate keys.** Every dimension has an integer `sk_<entity>` primary key and keeps its
  natural key. A fact has one `sk_<entity>` (or `sk_<role>`) column per dimension, all `NOT NULL`:
  load an unknown member for rows without a dimension.
- **Conformed dimensions.** One `dim_<entity>` table, shared by every fact that uses the entity
  (a note in the result lists them). A `role` gives a role-playing use of the same dimension.
- **Date dimension.** Every date attribute becomes an `sk_<attribute>` column pointing at
  `dim_date`, whose columns are those of the `star` transform (`shape.dimensional`).
- **Junk dimension.** The `junk` flags go into `dim_<fact>_junk` with an `sk_junk` key.
- **Degenerate dimensions.** `degenerate` attributes stay on the fact.
- **Many-to-many.** `many_to_many` entities get a `bridge_<fact>_<entity>` table (group key,
  member key, `weighting_factor`); the fact carries the group key, which has no foreign key
  because many bridge rows share it.
- **Slowly changing dimensions.** Type 2 adds `valid_from`, `valid_to` and `is_current`; type 3
  adds `previous_<attribute>`; types 0 and 1 add nothing. Columns go on the table that holds the
  attribute.
- **Snowflake.** Each hierarchy level above the finest, with the attributes it determines, moves
  to an outrigger table `dim_<entity>_<level>` (`sk_<entity>_<level>`), chained by surrogate keys.
  The date dimension and the facts are unchanged.

Two tables of a star or snowflake that get the same name (an entity called `date` next to the date
dimension, two entities or facts whose names differ only in case, an outrigger
`dim_<entity>_<level>` next to an entity called `<entity>_<level>`) are an error that names both,
never one table silently replacing the other.

## DDL

`shape.design.ddl.emit_ddl` writes `CREATE TABLE` through the SQL sink's table emitter (so types
are spelled as in a generated INSERT script), for its dialects: `tsql`, `tsql-fabric-warehouse`,
`postgres` and `mysql`. Foreign keys follow as `ALTER TABLE ... ADD CONSTRAINT`; on Fabric
Warehouse, which does not enforce constraints, they are `NOT ENFORCED`. `--drop` adds
`DROP TABLE` statements. `--schema-name` qualifies the tables. The text has no timestamp and no
version.

## Lint

| Code | Severity | Meaning |
|---|---|---|
| D001 | error | a fact has no declared grain |
| D002 | error | the grain does not determine an attribute the fact uses |
| D003 | error | a grain attribute has no column in the fact |
| M001 | warning | a measure has no declared additivity |
| M002 | warning | a semi-additive measure does not say what it is not additive over |
| M003 | warning | an additive measure is not numeric |
| F001 | warning | a reference attribute of the fact's source is neither a dimension nor degenerate |
| F002 | error | a dimension is reached through an attribute that references another entity |
| K001 | warning | an entity declares no key |
| N001 | warning | a name is not a plain identifier |
| N002 | error | a table or column name is longer than 63 characters |
| E001 | info | no fact uses an entity (star and snowflake) |
| G001 | error | the schema cannot be derived |

Findings are sorted by severity, code and path.

## Determinism

The output depends on the input only. Attribute order follows the input, never set iteration, so
it does not change with `PYTHONHASHSEED`; there is no clock and no random number. Tests compare
two runs byte for byte, and runs under different hash seeds.

## Building the input from data

`shape design DATA --from-data` (`shape.design.from_data.design_from_rows`) infers types,
nullability and string lengths, finds candidate keys with the code behind `shape key`, and exact
functional dependencies with the code behind `shape fd`. A dependency is reported only when no
group violates it and its determinant repeats (at most `max_group_ratio`, 0.5, distinct values per
row); keys, supersets of keys, constant columns, determinants with nulls and non-minimal
determinants are left out. The output is one entity per table with keys and dependencies. Facts,
measures, hierarchies, history and references are not in the data: add them by hand.
