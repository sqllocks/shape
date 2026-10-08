# `shape explain` and notebook display

## `shape explain`

```
shape explain DIFF.json [--json] [--classified COL1,COL2]
    [--baseline BASE.shape] [--current CURRENT.shape]
```

`DIFF.json` is what `shape diff A.shape B.shape --json DIFF.json` writes, or the output of
`shape drift`. The command prints a plain-English narrative: how many changes, in which columns
(most severe first), what moved and by how much (slight, moderate, large or very large, from the
change's 0 to 1 score) and the likely kinds of cause for the kinds of change found.

- **Deterministic.** The text is built from fixed templates. The same input gives the same text.
  There is no model and no network access.
- **`--json`** prints the structure under the text: `format` (`shape.explain`), `version` (1),
  `kind` (`diff` or `drift`), `drifted`, `counts` (changes, columns, by severity), `columns` (each
  with `column`, `withheld` and its `changes`: `kind`, `severity`, `score`, `size`, `sentence`),
  `likely_causes` and `text`.
- **Exit codes.** 0 on success; 2 when the file is missing, is not JSON, or is not a diff or a
  drift report.

### Classified columns

The narrative never prints a raw value:

- A range change, a new-category change or a pattern change reports a count or a plain statement,
  never the extremes, labels or pattern, for any column.
- A **withheld** column keeps its change kinds, severities and scores and nothing else. A column is
  withheld when `--classified` names it (a name, `table.column` or a glob), or when the
  safe-profile rules (`docs/PRIVACY_MODEL.md`) mark it pattern-only or sensitive in the profile
  given as `--baseline` or `--current`.

Pass the two profiles whenever you have them: a diff file alone cannot say which columns hold
personal data, so only `--classified` and the value-free rules above protect it.

## Notebook display

`Profile`, `DiffResult` (`shape.diff(...)`), `CheckResult` (`shape.check(...)`) and the drift
report of `shape.fidelity.tier3.DriftMonitor` define `_repr_html_` and `_repr_markdown_`, so a
Jupyter or Fabric notebook shows them as small tables (at most 25 rows, then "… n more").

- A profile shows each column's type, null rate and distinct count, and the mean for a column the
  safe-profile rules do not reduce to a pattern. It never shows an extreme, a category or a top
  value.
- A diff or drift report shows the same rows as `shape explain`.
- A check result shows expected and observed values only for aggregate rules (`dtype`,
  `nullable`, `unique`, `max_null_rate`, `distribution`, the true-rate rules); the rules that
  compare values (`min`, `max`, `allowed_values`, `pattern`) show `withheld`.
- Everything rendered is escaped: HTML with `html.escape`, Markdown with entities for `&`, `<`
  and `>` and backslashes for the other markup.
