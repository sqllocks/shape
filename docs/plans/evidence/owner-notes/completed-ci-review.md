# Completed CI evidence review — unresolved receipt gaps

Read completed run [38206127943](https://github.com/sqllocks/shape/actions/runs/38206127943), commit `304efc646f8ff8572454d83f7a3f8b7c129f2bcf` on main. This is historical evidence, not polling the lead's integration run. Logs retain GitHub's UTC timestamps; ANSI colour escapes and trailing line whitespace are stripped. No complete service transcript is inferred from a passing suite.

## DuckDB Delta extension / fallback

Job [114669972531, zero-network](https://github.com/sqllocks/shape/actions/runs/38206127943/job/114669972531) concluded success. Relevant actual excerpts:

```text
2026-10-12T06:09:17.6546624Z ##[group]Run sudo env "PATH=$PATH" python -c "import duckdb; duckdb.sql('INSTALL delta')"
2026-10-12T06:09:17.6547254Z sudo env "PATH=$PATH" python -c "import duckdb; duckdb.sql('INSTALL delta')"
2026-10-12T06:37:53.4278562Z tests/profile/test_delta_fallback.py::test_the_cli_profiles_a_deletion_vector_table
2026-10-12T06:37:55.5497376Z 17906 passed, 25 skipped, 69 deselected, 973 warnings in 1714.11s (0:28:34)
```

The workflow installs the extension before removing networking (`.github/workflows/ci.yml:124–127`). `tests/profile/test_delta_fallback.py:47–63` installs and loads it; its real-reader and CLI tests assert retained fixture rows and fallback behaviour. The log proves an install step and test execution, but does not show the complete fresh `INSTALL delta` / `LOAD delta`, `shape profile ...` and `shape cat ...` receipt requested in OWNER_NOTES_TRIAGE.md. At this historical CI-only review, the Delta note could not yet close; runbook D names the fresh extension-download and committed deletion-vector fixture work. The initial local inspection reported Delta not installed. The later [fresh local install/profile/cat receipt](delta-extension-fallback.md), using published dependency software and no live data account, completes that missing evidence and resolves the note.

## VS Code

Job [114669972473, vscode-extension](https://github.com/sqllocks/shape/actions/runs/38206127943/job/114669972473) concluded success. Relevant actual excerpts:

```text
2026-10-12T05:05:19.7544393Z   26 passing (1s)
2026-10-12T05:05:38.7011459Z     ✔ opens a .shape file as read-only text from `shape cat` (411ms)
2026-10-12T05:05:38.8880502Z     ✔ opens a real .shape file in the text view through the custom editor (150ms)
2026-10-12T05:05:39.4477982Z   5 passing (2s)
2026-10-12T05:05:39.6715714Z > node scripts/build.js && cd stage && npx --no-install vsce package --no-dependencies --out ../shape.vsix
2026-10-12T05:05:41.6591712Z Files included in the VSIX:
2026-10-12T05:05:41.6592313Z shape.vsix
2026-10-12T05:05:41.6593982Z ├─ extension.vsixmanifest
2026-10-12T05:05:41.9045098Z Packaged: ../shape.vsix (11 files, 18.96 KB)
2026-10-12T05:05:41.9428917Z   path: editors/vscode/shape.vsix
```

`editors/vscode/test/runSuite.ts:17–30` installs the YAML extension and loads Shape via `extensionDevelopmentPath` from the development stage. That is not `code --install-extension editors/vscode/shape.vsix`; packaging a VSIX does not prove package installation. That CI log alone could not close `docs/VSCODE.md:25`; the later [actual installed-VSIX/editor receipt](vscode-install.md) supplies the missing evidence and resolves it. Runbook D requires VS Code and the built repository VSIX in a clean installation, followed by installed-version and real project/artifact-view receipts.

## Release evidence

The public GitHub release lookup for `v0.9.1` returned 404. The completed Publish run `38168312038` and annotated-tag evidence establish release activity, but do not supply the literal `v1.0.0` tag/push or archive hash/attestation-verification transcripts requested by the two RELEASE_CHECKLIST notes. Those remain open; no tag is created and no release or repository setting is changed.
