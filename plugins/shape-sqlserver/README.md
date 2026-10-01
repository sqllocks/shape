# sqllocks-shape-sqlserver

Shape plugin: a SQL Server source and sink.

This distribution is a **skeleton**: it builds and installs, declares plugin API 1.x and
registers nothing yet. The work package that implements it adds the entry points to
`pyproject.toml` and the code under `src/shape_sqlserver/`, and tests it with `shape.plugins.kit`.

Its version always equals core's (`sqllocks-shape`), and it is released together with core.
How plugins are written: `docs/plugins/authoring.md` in the repository.
