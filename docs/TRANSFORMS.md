# Transforms: star schema and CDM

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


`shape transform` reshapes a set of related tables. The source is an installed domain (generated
first, with `--scale` and `--seed`) or a directory of CSV, Parquet or JSON Lines files, one per
table.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Both commands exit 0, or 2 for bad input (a missing table or column in the map, an unreadable
map, an unknown domain). `--json` prints the summary as JSON.

## `shape transform star`

Writes one file per table, in this order:

| Table | Contents |
|---|---|
| `dim_*` | the dimension: `sk_<name>` (1, 2, 3, ...) first, then the source columns. Rows with a repeated natural key are dropped after the first. |
| `dim_date` | one row per day from the first to the last date any fact uses: `sk_date` (`yyyymmdd`), `date`, calendar and fiscal attributes. |
| `fact_*` | the source rows, each foreign key replaced by `nk_<column>` (the natural key) and `sk_<dimension>` (a surrogate key, null when the key is null or has no dimension row), and `sk_date` from a date column. |

A row whose non-null key has no dimension row is reported on stderr and in `orphans`; a null
key is not an orphan.

### Star map

```json
{
  "dimensions": {
    "dim_product": {
      "source": "product", "key": "sk_product", "natural_key": "product_id",
      "enrich": [{"table": "product_category", "left": "category_id", "prefix": "cat_"}],
      "columns": ["product_id", "product_name"]
    }
  },
  "facts": {
    "fact_sale": {
      "source": "order_line",
      "join": [{"table": "order", "left": "order_id", "right": "order_id"}],
      "dimension_keys": {"product_id": "dim_product"},
      "date_columns": ["order_date"],
      "columns": ["order_id", "sk_product", "sk_date"]
    }
  },
  "date_dimension": true,
  "fiscal_year_start": 1
}
```

`right` defaults to `left`. `columns` (optional) selects and orders the output. A fact with several
`date_columns` gets `sk_date_<column>` for each; with one it gets `sk_date`.

The map is checked strictly: an unknown field, a table or column that is not there, a join that
would repeat fact rows (a non-unique key on the other side), two columns replaced by one
dimension, a joined column that collides with an existing one, and dates spanning over 60 years
are errors, not silent changes of the output.

A domain brings its own map (`retail` does); others are added by the domain plugins.

## `shape transform cdm`

Writes a Common Data Model folder: `<Entity>/<Entity>.csv` (or `.parquet`) per table and a
`model.json` listing each entity's attributes (`int64`, `double`, `decimal`, `boolean`, `dateTime`,
`date`, `time`, `string`, from the column's type) and its data partition. Entity names come from
the domain, from `--map` (`{"entities": {"customer": "Contact"}}`), or are the table name in
PascalCase. Two tables that become one entity are an error. `--model-name` sets the model name
(default `Shape<Domain>`).

## From Python

```python
from shape.dimensional import StarMap, star_transform, write_cdm_folder

result = star_transform(tables, StarMap.from_dict(star_map))   # tables: name -> pyarrow.Table
result.tables()                                                # dims, dim_date, facts
write_cdm_folder(tables, "cdm/", "MyModel", {"customer": "Contact"})
```

The same transforms are plugins: `host.get("shape.transforms", "star").apply(tables, map=...)`.

## Relation to `generate --mode star`

`shape generate DOMAIN --mode star` selects a domain's *generation* schema for the star layout;
`transform star` is post-processing of existing tables. They share no code: one decides which
values to generate, the other how to join and key values that already exist.
