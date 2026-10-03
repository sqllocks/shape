# Shape by SQLLocks for VS Code

A small extension for [Shape](https://github.com/sqllocks/shape) projects.

## What it does

- **`shape.yml` completion and validation.** The bundled JSON Schema
  (`shape-project-v1.schema.json`, the one the `sqllocks-shape` package ships) is attached to
  `shape.yml` and `shape.yaml`. Completion, hover and error squiggles come from the Red Hat YAML
  extension, which is installed with this one.
- **Generation spec validation.** `*.gen.json` files are checked against the generation spec
  schema, in builds of the extension made from a repository that has it.
- **Snippets** for `shape.yml`: `shape-project`, `shape-source`, the five baseline kinds
  (`shape-baseline-previous`, `-weekday`, `-window`, `-monthend`, `-pinned-artifact`,
  `-pinned-ref`), `shape-column-threshold`, `shape-gate-observe`, `shape-gate-enforce` and
  `shape-owner`.
- **A readable `.shape` file.** Opening a `.shape` file shows the text form that `shape cat FILE`
  prints (one `path: value` line per property) in a read-only editor, instead of a binary blob.
  **Shape: Compare with Git HEAD** (Command Palette, or right-click a `.shape` file) shows a diff of
  the committed and the working copy's text forms.

## What it does not do

It does not run generation, profiling or checks, has no panels, tree views or language server, and
supports no other file type. It sends no telemetry and makes no network request. The only programs
it starts are `shape` (`--version` and `cat`) and `git` (`rev-parse`, `show`).

## Requirements

- Visual Studio Code 1.90 or newer.
- The `shape` command line, version **0.9.0** or newer (`pip install sqllocks-shape`), for the
  `.shape` view and the comparison. If it is missing or too old the extension shows one error that
  names the `shape.path` setting and the minimum version, and runs nothing else.
- `git` on `PATH` for **Compare with Git HEAD**.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `shape.path` | empty | Path to the `shape` executable. Empty: find `shape` on `PATH`. A user-level setting only: a repository cannot set it. |

## Install from a `.vsix`

```bash
code --install-extension shape.vsix
```

The `.vsix` is built by CI (artifact `shape-vscode`) or locally (below). The extension is not on
a marketplace from this repository's CI; see `docs/RELEASE_POLICY.md` for the manual release step.

## Build and test

```bash
cd editors/vscode
npm ci
npm run build      # copies the schemas from src/shape/schemas, compiles, stages stage/
npm test           # unit tests, then the extension tests in VS Code (xvfb-run -a npm test on Linux)
npm run package    # shape.vsix
```

`npm run build` copies the schemas from `src/shape/schemas/`, so the extension never carries a
stale copy; a test in the Python suite fails when `editors/vscode/schemas/` differs from them.
