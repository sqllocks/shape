# Excel workbooks

`.xlsx` workbooks are read as a source (`shape profile book.xlsx`, and every command that reads a table file) and written
as a sink (`shape generate ... -f excel`). Both need the `[excel]` extra (openpyxl): `pip install 'sqllocks-shape[excel]'`.
Each statement below has a test in `tests/excel/`.

## Reading a workbook

```bash
shape profile book.xlsx -o book.shape                  # one table per visible sheet (a dataset)
shape profile 'book.xlsx#Members' -o members.shape     # that sheet alone (hidden or not)
shape profile book.xlsx --sheet Members -o members.shape
shape profile 'book.xlsx#Members' -o t.shape           # an Excel table or a named range called Members
shape profile book.xlsx --include-hidden -o all.shape  # read the hidden sheets too
```

```python
shape.profile("book.xlsx")                     # dataset
shape.profile("book.xlsx#Members")              # one table, named after the sheet
shape.io.open_source("book.xlsx#Members")       # Arrow batches; open_workbook("book.xlsx") gives every sheet
```

`reference_pairs` works on a workbook as on any source: a list for one sheet, and a dict of sheet name to list for the
whole workbook. The Delta options (`version`, `as_of`) and the CSV options (`delimiter`, `encoding`, `quotechar`,
`header=False`) do not apply to a workbook and are refused by name.

The first non-empty row of a sheet is its header. Sheets are read in read-only streaming mode. Cached formula results
are read, not the formulas. `.xls` (legacy), `.xlsb` and password-protected files are refused with an error that says
what to do; an archive that inflates absurdly is refused as well.

### Tables and named ranges

`book.xlsx#Name` also takes the name of an Excel table (Insert > Table) or of a named range (a defined name that points
at one block of cells, such as `Data!$B$3:$D$6`). The first row of the block is its header, the table is named after
the table or range, and findings carry cell positions on the sheet. A sheet of that name wins over a table, and a table
over a named range; a name is matched exactly first, then ignoring case. A table's totals row is left out. These are
refused with a clear error: a table with no header row, a named range of several areas, of one cell, or of a sheet that
does not exist. The same works through `shape.io.open_source`, `shape.profile` and `read_workbook(path, "Name")`.

### Merged cells

A merged range reads as the value of its top-left cell; the other cells of the range are blank. The `merged_cells`
finding lists the ranges (`ranges`), their top-left cells (`cells`), how many `count` and how many cells they leave
`blank_cells`. For a table or named range only the merges inside it are reported.

### Cell types are kept

A cell stored as text stays text, so identifiers such as ZIP codes, NDCs and member ids keep their leading zeros: a
text column is never turned into numbers or dates by the profile, and `shape generate --from` regenerates a text column
of fixed-width digits that has leading zeros (ZIP, member id, ...) as zero-padded text (random digits, or counting up
when the column is unique). Numbers, dates, booleans and blanks come through as their Excel types; a column that mixes
text with other types becomes text. An error cell (`#N/A`, ...) is a missing value.

### Findings

The profile carries a `findings` list (on the table it is about, and on the dataset for the workbook itself) only when
there is something to report. A profile of any other source has no `findings` key. Each finding has a `kind`, the
`table` (sheet) and `column`, a `count`, a `share` of the column's non-blank cells, up to 10 cell positions (`cells`) and
5 `examples`.

| `kind` | What it reports |
|---|---|
| `numbers_stored_as_text` | text cells that read as numbers (`leading_zeros`: how many keep a zero; the column stays text) |
| `dates_stored_as_text` | text cells that read as dates (ISO, `m/d/yyyy`, `d.m.yyyy`, `31-Jan-2024`, `January 31, 2024`) |
| `hidden_column` | a hidden column (still read) |
| `hidden_sheet` | a hidden or very hidden sheet, with `state` and whether it was `read` |
| `error_cells` | `#REF!`, `#N/A`, `#VALUE!`, `#DIV/0!`, `#NAME?`, `#NUM!`, `#NULL!`, with `by_error` counts |
| `duplicate_header` | a repeated header, renamed `name_2`, `name_3`, ... (the new name is `column`, the old one `original`) |
| `blank_header` | a column with no header, named `column_N` |
| `sentinel_values` | a placeholder: `99999` and other all-nines numbers, `00000`, `9999-12-31` and other epoch dates, `N/A`, `NULL`, `TBD`, ... (`value`) |
| `merged_cells` | merged ranges (`ranges`, `cells`, `count`, `blank_cells`): the value is the top-left cell's, the rest are blank |
| `mixed_types` | a column that mixes text with numbers or dates (`types`) |

`shape profile --json` includes the findings in the summary. The share-safe form (`shape profile safe`) leaves them out,
because their examples are cell values.

## Writing a workbook

```bash
shape generate retail --scale small -f excel -o out/                       # out/retail.xlsx
shape generate retail -f excel -o out/ --chaos-log out/_chaos_ground_truth.jsonl --drift-plan plan.json
```

```python
from shape.generation.output import write_engine, write_result
write_result(result, "excel", "out/")        # out/<domain>.xlsx
```

The `excel` format writes **one workbook** for the whole dataset (`<domain>.xlsx`; the `workbook` option names it):

- **One sheet per table**, in dependency order. Sheet names are made valid (at most 31 characters, none of `[]:*?/\`, no
  leading or trailing apostrophe, not `History`) and unique ignoring case (`orders`, `orders_2`); a table that has to be
  renamed is listed with its sheet in the `_README`.
- **`_README`**, the first sheet: Shape version, domain, schema mode, seed, scale, generation time (UTC) and duration,
  rows and columns per table, the table-to-sheet mapping, the text-format identifier columns, and what was planted: the
  chaos in the ground-truth log (`--chaos-log`, from `shape chaos`: the changes per table, kind and column, with an
  example) and the drift plan or its answer key (`--drift-plan`: every event). With neither, it says `none`.
- **Formatting**: a styled header row, frozen, with an autofilter over the table (`write_workbook(..., autofilter=False)`,
  or `autofilter=False` in `write_result`/`write_engine` options, turns it off); column widths fitted to the first 1,000 rows and capped at 50; dates and
  timestamps have a date format; **identifier columns have the text number format `@`** so leading zeros survive editing
  in Excel: text columns whose values are all digits (ZIP, NPI, member id), text columns named like an identifier (`id`,
  `key`, `code`, `zip`, `ndc`, `npi`, `mrn`, `ssn`, `member`, `account`, `number`) and the text key columns of the
  schema. Integer keys stay numbers.
- Text that Excel would read as a formula (`=1+1`) or an error (`#N/A`) is stored as text.
- **Limits**: a sheet holds 1,048,576 rows (header included) and 16,384 columns. A larger table is refused before
  anything is written, with an error that names the table (`WorkbookTooLargeError`): write it as csv or parquet, or
  generate fewer rows. There is no automatic split.

The per-table sink (`shape.sinks` `excel`, `write(uri, table, batches)`, `<table>.xlsx`) is unchanged for code that calls it
directly; `write_workbook` is the multi-sheet form, and a workbook it wrote reads back with the source above (identifier
columns come back as text).
