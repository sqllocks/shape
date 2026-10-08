# 1.0 Release Policy
Semantic Versioning applies to the normative Shape contract and stable CLI exit classes. Patch releases may fix defects without weakening safety. Minor releases may add optional capabilities. New mandatory semantics require an explicitly versioned capability and may require a major release. Deprecations require a migration path.

How a release is built, published and checked, and how to roll one back: the [release checklist](RELEASE_CHECKLIST.md).

Persisted files, and what a release must keep reading, are governed by [the state and compatibility policy](specs/STATE_AND_COMPATIBILITY.md): a format version is dropped only in a new major release, announced a release cycle ahead in the changelog under "Deprecated", and only if the offline `shape-migrate` path still reads it.

## The VS Code extension (`editors/vscode/`)
CI builds the extension, runs its tests and attaches the `.vsix` to the run as the artifact `shape-vscode`; it never publishes. Publishing to the Visual Studio Marketplace or Open VSX is a manual release step, done by a maintainer:
1. On the release commit, run `cd editors/vscode && npm ci && npm run build && npm test && npm run package`. `npm run build` copies the schemas from `src/shape/schemas/`, so the packaged copies match the Python package of the same commit.
2. Check `package.json` (`publisher` `sqllocks`, `name` `shape`, `version`) against `docs/BRANDING.md`, and that `engines.vscode` and the minimum `shape` version (`MIN_VERSION` in `src/cli.ts`) are still right.
3. Install `shape.vsix` into a clean VS Code (`code --install-extension shape.vsix`) and open a `shape.yml` and a `.shape` file.
4. Publish that same `.vsix` with `npx @vscode/vsce publish --packagePath shape.vsix` (Marketplace, a personal access token of the `sqllocks` publisher) and `npx ovsx publish shape.vsix` (Open VSX). Tokens are the maintainer's; none is stored in the repository or in CI.
5. The extension's version is its own (it starts at 0.1.0) and follows semantic versioning of its settings and commands. Raising `MIN_VERSION` is a minor version of the extension.
