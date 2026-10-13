# Proposal: layout options for the workbook sink

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`. Lower priority: a short proposal.

## 1. The problem

Spreadsheets that people exchange rarely start at cell A1 with a plain header. A report export has a title banner and a run date above the header, a monthly template has grouped headers merged over several columns, a finance sheet has a totals row, and a vendor file has hidden helper columns. Testing a loader for such files needs generated workbooks with the same layout.

- The workbook writer has the options `meta`, `identifier_columns_by_table`, `chaos_log`, `drift_plan` and `autofilter` only (`src/shape/builtins/sinks/workbook.py:348` to `365`).
- The layout is fixed: one sheet per table, header in row 1, frozen, column widths fitted (`docs/EXCEL.md:96` to `120`; `freeze_panes="A2"` at `workbook.py:412`).
- The read side already handles merged cells, hidden columns and totals rows (`docs/EXCEL.md:45` to `80`), so files of that shape can be profiled but not produced.

## 2. Proposed design

A `layout` option (Python `write_workbook(..., layout=...)`, CLI `--workbook-layout FILE.json`), per sheet:

```json
{"format": "shape-workbook-layout", "version": 1,
 "sheets": {"order": {"sheet": "Orders 2024",
   "banner": [["Monthly orders report"], ["Generated", "{run_date}"]],
   "header_row": 4, "first_column": "B",
   "columns": [{"column": "order_id", "header": "Order #"}, {"column": "amount", "header": "Amount", "number_format": "#,##0.00"},
               {"column": "internal_ref", "hidden": true}],
   "header_groups": [{"label": "Order", "columns": ["order_id", "order_date"]}],
   "totals": {"label": "Total", "columns": {"amount": "sum"}, "formula": true}}}}
```

- `columns` renames, reorders and selects columns; a column left out is not written. `hidden` hides it.
- `banner` rows above the header (with `{run_date}`, `{seed}`, `{rows}` placeholders, fixed per run for byte identity), `header_row` and `first_column` offset the table.
- `header_groups` writes a merged row above the header.
- `totals` writes a row after the data, as Excel formulas (`"formula": true`) or as values.
- Freeze panes and the autofilter follow the moved header.

## 3. Compatibility

Opt-in; without `layout` the workbook is byte-identical to today's. New format `shape-workbook-layout` with `format` and integer `version`. The row limit check (`docs/EXCEL.md:118`) counts banner, header groups and totals rows.

## 4. Wanted (ready to paste into the issue)

1. A layout format `shape-workbook-layout` version 1 with the per-sheet keys of section 2, validated strictly (unknown column, overlapping banner and header, a group over columns that are not adjacent).
2. `write_workbook(..., layout=...)`, `write_result` options and `--workbook-layout` on the commands that write `excel`.
3. Freeze panes, autofilter and identifier text format applied at the moved header.
4. Documentation in `docs/EXCEL.md` with one example per option.

Acceptance beyond section 13's common items: reading a laid-out workbook back with the Excel source (with its header and merged-cell options) gives the original rows and the renamed columns; totals equal the column sums; without `layout` output is byte-identical before and after the change.

## 5. Open questions for the lead
1. Whether several tables per sheet (side by side or stacked) are wanted, which the reader would then also need.
2. Whether this belongs with W2-04 (Excel named ranges, tables, merged cells).
