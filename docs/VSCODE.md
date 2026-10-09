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
<!-- owner: editor maintainer — run a VS Code install transcript before publishing an install command. -->

## Related

[Project configuration](PROJECT.md) · [Drift tutorial](tutorials/03-drift.md) · [CLI](CLI.md)
