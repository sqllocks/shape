# VS Code extension

Read and edit project configuration and inspect shape artifacts as text.

Status: available.

The extension attaches the bundled project JSON Schema to `shape.yml` and `shape.yaml`.
The YAML extension supplies validation, completion and hover help. Snippets create project,
source, baseline, threshold, gate and owner entries. Generation JSON specs have schema
validation when the generation schema is present in the built extension.

Opening a `.shape` file shows the text produced by `shape cat` in a read-only editor.
The Compare with Git HEAD command shows committed and working text forms in the editor's diff.
The extension starts local `shape` and `git` commands for those views. It does not profile,
generate or run checks. It has no telemetry code or remote-request code in its entry point.

## Requirements and evidence

The extension manifest requires VS Code 1.90 or newer. Artifact viewing requires the Shape
CLI; the code enforces a minimum of 0.9.0.  Git is needed for comparisons.
`shape.path` is a user-level executable setting, not a repository-controlled setting.

Evidence: `editors/vscode/package.json`, `editors/vscode/src/extension.ts`, the bundled schemas
and snippets. The repository workflow builds a `.vsix`; this page makes no marketplace claim.
The workflow builds the VSIX from this checkout, installs it into an isolated VS Code instance, then runs **Shape: Compare with Git HEAD** in that installed extension against a real `.shape` artifact and the real Shape CLI. The runner supplies the paths below. The command driver asserts that a diff editor opens and that the two text forms differ.

<!-- owner-example: vscode-install -->

```bash {.runnable-owner}
code --install-extension "$SHAPE_DOCS_VSIX" --force \
  --user-data-dir "$SHAPE_DOCS_VSCODE_USER" --extensions-dir "$SHAPE_DOCS_VSCODE_EXTENSIONS"
xvfb-run -a node "$SHAPE_DOCS_REPO/scripts/docs_vscode_command.cjs"
```

Evidence: [run 38273696299, ubuntu-latest, `7e0aa725e228`](https://github.com/sqllocks/shape/blob/docs/transcripts-ci/docs/plans/evidence/owner-notes/docs-transcripts-38273696299-7e0aa725e228-ubuntu-latest-vscode-install.json).

## Related

[Project configuration](PROJECT.md) · [Drift tutorial](tutorials/03-drift.md) · [CLI](CLI.md)
