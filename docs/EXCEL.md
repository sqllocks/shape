# Excel workbooks

`.xlsx` workbooks are read as a source (`shape profile book.xlsx`, and every command that reads a table file) and written
as a sink (`shape generate ... -f excel`). Both need the `[excel]` extra (openpyxl): `pip install 'sqllocks-shape[excel]'`.
Each statement below has a test in `tests/excel/`.

## Reading a workbook

```bash
shape profile book.xlsx -o book.shape                  # one table per visible sheet (a dataset)
shape profile 'book.xlsx#Members' -o members.shape     # that sheet alone (hidden or not)
shape profile book.xlsx --sheet Members -o members.shape
shape profile book.xlsx --include-hidden -o all.shape  # read the hidden sheets too
```

```python
shape.profile("book.xlsx")                     # dataset
shape.profile("book.xlsx#Members")              # one table, named after the sheet
shape.io.open_source("book.xlsx#Members")       # Arrow batches; open_workbook("book.xlsx") gives every sheet
```

The first non-empty row of a sheet is its header. Sheets are read in read-only streaming mode. Cached formula results
are read, not the formulas. `.xls` (legacy), `.xlsb` and password-protected files are refused with an error that says
what to do; an archive that inflates absurdly is refused as well.

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
| `mixed_types` | a column that mixes text with numbers or dates (`types`) |

`shape profile --json` includes the findings in the summary. The share-safe form (`shape profile safe`) leaves them out,
because their examples are cell values.
