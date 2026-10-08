# File sources

A source turns a file or URI into Arrow record batches. Every command that reads data (`profile`,
`check`, `diff`, `generate --from`) goes through them. This page lists the file sources and gives
the options of the two that read nested documents: `json` and `xml`. The plugin API for writing
your own source is [plugins/api-v1.md](plugins/api-v1.md); the built-in list is
[plugins/builtins.md](plugins/builtins.md).

| Source | Files | Reads as |
|---|---|---|
| `csv` | `.csv`, `.tsv` (compression suffixes allowed) | one flat table |
| `parquet` | `.parquet`, `.pq` | one flat table |
| `jsonl` | `.jsonl`, `.ndjson` | one table; `flatten="tables"` gives related tables |
| `json` | `.json` | one table, or related tables (below) |
| `xml` | `.xml` | related tables (below) |
| `ipc` | `.arrow`, `.ipc`, `.feather` | one flat table |
| `abfss`, `delta` | OneLake / ADLS Gen2 and Delta tables | see [plugins/cloud-sources.md](plugins/cloud-sources.md) |
| `x12`, `hl7v2` | `.x12`, `.edi`, `.hl7` | segment tables (healthcare plugin, see [plugins/healthcare-standards.md](plugins/healthcare-standards.md)) |

Nested sources add three methods to the Source protocol. `schema(uri, **options)` and
`read(uri, **options)` give ONE table: the root, or the one named by the `table` option.

```python
from shape.builtins.sources import JsonSource

src = JsonSource()
tables = src.read_tables("orders.json", flatten="tables")        # dict[str, pyarrow.Table]
links = src.read_relationships("orders.json", flatten="tables")  # list[Relationship]
result = src.read_nested("orders.json", flatten="tables")        # tables, relationships, warnings
```

`read_nested` parses once and returns a `NestedTables` (`.tables`, `.relationships`, `.warnings`);
the other two are shortcuts for its fields. Each relationship is a
`shape.generation.schema.Relationship` (`one_to_many`, parent column `_id`, child column
`_parent_id`), which a generation spec uses as it is. To profile every table, call
`shape.profile(table)` on each one.

## `json`: one document or an array of documents

A `.json` file holds one JSON object, or an array of objects; each object is one row of the root
table. A file that holds anything else (a number, a string, an array of numbers) raises
`ReaderError`. Invalid JSON, a file that is not UTF-8 and a file over a limit (below) raise
`ReaderError` too.

Options (`json`; `jsonl` takes `flatten`, `name`, `table` and `max_bytes` as well):

| Option | Default | Meaning |
|---|---|---|
| `flatten` | `"struct"` | `"struct"` or `"tables"`, below |
| `name` | the file name up to the first `.` | name of the root table |
| `table` | the root | which table `schema` and `read` return (`read_tables` always returns all) |
| `max_bytes` | 268435456 (256 MiB) | the largest file read; a bigger one raises `ReaderError` naming the limit |

### `flatten="struct"`

One table. A nested object stays an Arrow struct and an array stays a list, with the types Arrow
infers (`customer: struct<name: string, ...>`, `tags: list<string>`). Documents whose values for
one field have different types (`[1, "x"]`) cannot be a struct column: the reader raises
`ReaderError` and says to use `flatten="tables"`. `jsonl` with no `flatten` is this mode, as it
always was.

### `flatten="tables"`

One table per level, named after the root (`orders` below).

```json
[{"id": 1, "customer": {"name": "Ana", "address": {"city": "Oslo"}},
  "tags": ["new", "vip"],
  "lines": [{"sku": "A1", "qty": 2}, {"sku": "B2", "qty": 1}]}]
```

| Table | Columns | Rows |
|---|---|---|
| `orders` | `_id`, `id`, `customer.name`, `customer.address.city` | `1, 1, Ana, Oslo` |
| `orders__tags` | `_id`, `_parent_id`, `_ordinal`, `value` | `1, 1, 0, new` and `2, 1, 1, vip` |
| `orders__lines` | `_id`, `_parent_id`, `_ordinal`, `sku`, `qty` | `1, 1, 0, A1, 2` and `2, 1, 1, B2, 1` |

* Every table has `_id`: 1, 2, 3 ... in document order.
* A nested object becomes prefixed columns joined by `.` (`address.city`, `address.geo.lat`).
* An array of objects becomes the child table `<parent>__<field>`; for an array inside a nested
  object the table name joins the path with `__` (`orders__customer__phones`). The child has
  `_parent_id` (the parent row's `_id`) and `_ordinal` (the position in the array, from 0).
* An array of scalars becomes a child table with a `value` column. An array of arrays becomes a
  child table whose `value` column is empty and whose own child table holds the inner array
  (`orders__matrix__value`).
* An array that mixes objects with scalars puts the objects' fields in their columns and the
  scalars in `value`.
* An empty array gives an empty child table (no rows, same columns); an empty document `{}` gives
  one row with only `_id`; an empty array of documents gives a root table with no rows and only
  `_id`.
* Column types: all `int` is `int64`, `int` and `float` together are `float64`, all `bool` is
  `bool`, text is `string`, and a column with no value but nulls is `string`. Any other mix of types in
  one column (a number and a string), or an integer too big for 64 bits, is read as `string`
  (non-strings as their JSON text) and a message naming the column is added to
  `NestedTables.warnings`.
* A missing key and a `null` are both null.
* Two different fields that would give the same table name (`a__b` and `{"a": {"b": ...}}`) raise
  `ReaderError`: rename one.

Each table's Arrow schema metadata carries `shape.nested.kind` (`root`, `objects` or `scalars`),
`shape.nested.parent` and `shape.nested.path` (JSON list of the keys from the parent record to the
array), which is what the test helper uses to put a document back together. The metadata is not a
persisted format; nothing reads it from disk.

### Limits and safety

Before a `.json` file is parsed its nesting is checked with `shape.security.jsondepth`
(`check_json_document`: across the whole document, however it is formatted); a JSON-lines file is
checked per line. Nesting deeper than 128 levels raises `ReaderError` ("nested deeper than 128
levels"), so a hostile file cannot crash the parser. A bracket inside a string counts, which can
only reject a pathological file. File size is checked against `max_bytes` first.

## `xml`

An XML file is read into the same kind of related tables. The reader uses the standard library
parser behind an entity check (`shape.security.xmlsafe`): a document that declares any entity
(`<!ENTITY`, in any encoding, including UTF-16) is refused with `UnsafeXml` before it is parsed, so
an entity expansion bomb cannot run, and nothing is fetched from the network, neither an external
entity nor an external DTD. Nesting deeper than 128 levels raises `ReaderError`; so does a file
over `max_bytes`, or one that is not well-formed.

Options:

| Option | Default | Meaning |
|---|---|---|
| `record` | the most repeated element under the root | the elements that become rows (below) |
| `name` | the record element's name | name of the root table |
| `table` | the root | which table `schema` and `read` return |
| `max_bytes` | 268435456 (256 MiB) | the largest file read |

`record` is a small path: names joined by `/` (a child) or `//` (any depth), `*` for any name.
`//order` is every `order` anywhere; `/shop/order` starts at the document; `order` and `./order`
start at the root element; `.//order` is every `order` below the root. Predicates (`[1]`,
`[@id='x']`) and everything else of XPath are not supported and raise `ReaderError`; so does a path
that matches nothing.

Without `record`, the root's child that repeats most often gives the rows, and a warning names it
(`UserWarning`, also in `NestedTables.warnings`). When no child repeats, the root element is the
one record.

For each record element:

| In the XML | In the table |
|---|---|
| attribute `id="1"` | column `@id` |
| a child with only text, `<city>Oslo</city>` | column `city` |
| a child with attributes or children, `<customer><name>..` | prefixed columns, `customer.name`, `customer.@id` |
| text of an element that also has attributes or children | column `#text` (the text before the first child) |
| a child that appears more than once inside its parent in any record | a child table `<record>__<child>`, with `_id`, `_parent_id`, `_ordinal`, as for JSON arrays |

Namespaces are dropped (local names are used) and every value is a string: nothing is parsed as a
number or a date. A repeated leaf element (`<tag>a</tag><tag>b</tag>`) is a child table with a
`value` column. Comments and processing instructions are ignored.

Example: with the file in the tests (`<shop>` holding two `<order id=..>` with `<line sku=..>`
children) `record="//order"` gives `order` (`_id`, `@id`, `customer.name`, ...) and `order__line`
(`_id`, `_parent_id`, `_ordinal`, `@sku`, `qty`).

Not supported: writing XML, XML Schema validation, streaming a document larger than memory.
