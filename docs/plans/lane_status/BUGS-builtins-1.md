# BUGS-builtins-1 status

Issues: #276 #280 #285 #287 #297.

- #276, #280: fixed (commits 83ac219, c5988bd).
- #285: T-SQL half fixed (line breaks in identifiers flattened to a space (refusing them would break the existing test `test_sql_names_and_headers_cannot_add_statements`, which expects a hostile name to be written); line breaks in T-SQL string
  values written as NCHAR/CHAR concatenations; `design/ddl.py` `kind` goes through `_one_line`).
  **Left open for the lead:** the PostgreSQL backslash half. Both remedies in the issue
  (`E'...'` literals, or a `SET standard_conforming_strings = on;` line) change the bytes pinned by
  `tests/generation/golden/writers/item.postgres.sql` (`'O''Brien \ é'`). Changing that golden would
  change an existing test's expectation, which this lane may not do.
