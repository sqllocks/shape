# Data dictionary: `shape dictionary`

Status: available.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


A human-readable description of a dataset's tables and columns, written from a profile and,
optionally, the project file. It reads the `.shape` profile and `shape.yml` only: it never reads
the data, makes no network request and pushes nothing to a catalog.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

| Flag | Meaning |
|---|---|
| `--format` | `md` (default), `html` or `json`. |
| `-o OUT` | The file to write (required; its folder is created). |
| `--examples` | Add example values and top values, for columns classified below `CONFIDENTIAL` only. |
| `--project FILE`, `--no-project`, `--source NAME` | As for the other commands (`docs/PROJECT.md`): `shape.yml` is found from the working folder upwards, the source is `--source`, else the only source, else the one named like the profile. |

The command prints one JSON line (`output`, `format`, `dictionary_version`, `tables`, `columns`).
Exit 0 on success; **exit 2** for a missing or unreadable profile (including a file that is not a
Shape profile), a `shape.yml` that is not valid, or a `--source` that is not in the project.

## What an entry holds

One entry per table (name, row count, primary key) and per column:

| Field (JSON) | From |
|---|---|
| `name`, `type` | the profile |
| `null_rate` | the profile |
| `distinct_estimate` | the profile's distinct count (an estimate when the profile was sampled) |
| `semantic` | `{label, confidence}` from the column name and the detected pattern (`shape.proposals`), or `null` |
| `classification` | see below |
| `numeric_range` | `{min, max}` of a numeric column; `null` otherwise and for `CONFIDENTIAL` or higher |
| `length_range` | `{min, max}` characters of a text column, or `null` |
| `format_pattern` | the pattern the profiler detected (`email`, `uuid`, ...), or `null` |
| `owner`, `annotations` | `shape.yml` (`sources.NAME.columns.COLUMN`); `null` and `{}` without a project |
| `top_values`, `examples` | only with `--examples` and only below `CONFIDENTIAL`: the five most frequent values with their share, and up to three example values (the minimum, the maximum, then the most frequent) |

## Classification and values

Every column gets a class from `shape.privacy.classification` before anything about its values is
written (`PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET`):

- `CONFIDENTIAL` when the safe profile's personal-data gate fires on the column (a personal-data
  pattern such as an e-mail address, a share of values matching one, or nearly every value distinct,
  which also catches keys and free text), or when a column-name or pattern proposal marks it as
  personal data;
- `INTERNAL` otherwise;
- an annotation `classification: LABEL` in `shape.yml` can only **raise** a column's class (an
  unknown label counts as `CONFIDENTIAL`).

A column at `CONFIDENTIAL` or above never gets a numeric range, example values or top values, in
any format, with or without `--examples`. Its type, null rate, distinct count, lengths, pattern
name, owner and annotations are still listed. Tests search every output for each top value, the
minimum and the maximum of such columns and fail on one.

## Output formats

**JSON** is the source of the other two:

```json
{
  "format": "shape-data-dictionary",
  "version": 1,
  "profile": "orders",
  "source": "orders",
  "examples_included": false,
  "tables": [{"name": "orders", "row_count": 200, "primary_key": ["id"], "columns": [{"name": "amount", "type": "float", "...": "..."}]}]
}
```

Markdown and HTML are rendered from that document. The same inputs always give the same bytes (no
timestamp, no path, sorted keys, tables sorted by name, columns in profile order). The HTML is one
self-contained file: inline style, no script, no external resource.

## Versioning

The JSON declares `format` and an integer `version` (now `1`). A change that an older reader could
misread raises the version; fields may be added without one. A document with a newer version than
the installed Shape reads is refused with an error that says to upgrade
(`shape.dictionary.render_markdown` and `render_html` accept any version-1 document).
`tests/fixtures/dictionary/v1/` holds a frozen version 1 document with its Markdown and HTML, which
every later Shape must still render byte for byte (`tests/dictionary/test_compat.py`).
