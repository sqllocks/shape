# Installed VSIX and real editor receipt

Status: available.

These are recorded local runs. Commands and results are historical evidence, not commands executed by the docs harness.

Captured **2026-10-12 UTC**, product base `8d10aa0a768383a947fa77d9547b066f8cfbd9cc`. Local Linux **6.18.44** virtual display (Xvfb, TCP disabled), VS Code **1.141.0**, installed Shape extension **0.1.0**, Red Hat YAML **1.25.0**; actual Shape CLI **0.9.1**, Python **3.12.14**, rust kernel. No VM, marketplace publication, live data service or signed-in account is used. Published software/build dependencies were downloaded with the session's proxy and CA verification; no TLS verification was disabled. Xvfb was extracted under /tmp, not installed as a system package.

## Build and actual package installation

The existing repository tooling ran `npm ci`, `npm run build`, `npm run package` from `editors/vscode`, each exit **0**. The latter built `editors/vscode/shape.vsix`; product source and package versions were not changed.

The public VS Code executable was downloaded with the repository's `@vscode/test-electron` tooling, using the configured HTTPS proxy and CA. The actual CLI commands below used new empty temporary user-data and extension directories; the installed package was the generated Shape VSIX. Core/editor versions are deliberately distinguished (0.9.1 vs 0.1.0).

Complete installation/version streams (ANSI colour stripped), each command exit **0**:

```text
COMMAND: code --version
1.141.0
2a59476c9bfcb90b3ddc372c36762471b7dfad1c
x64
Option 'user-data-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-user'.
Option 'extensions-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-extensions'.
exit: 0
COMMAND: code --install-extension /workspace/shape/editors/vscode/shape.vsix
Installing extensions...
Extension 'shape.vsix' was successfully installed.
Option 'user-data-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-user'.
Option 'extensions-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-extensions'.
(node:15830) [DEP0169] DeprecationWarning: `url.parse()` behavior is not standardized and prone to errors that have security implications. Use the WHATWG URL API instead. CVEs are not issued for `url.parse()` vulnerabilities.
(Use `code --trace-deprecation ...` to show where the warning was created)
exit: 0
COMMAND: code --list-extensions --show-versions
redhat.vscode-yaml@1.25.0
sqllocks.shape@0.1.0
Option 'user-data-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-user'.
Option 'extensions-dir' is defined more than once. Using value '/tmp/owner-notes2-vscode-extensions'.
exit: 0
```

## Real editor checks

A real two-row CSV (`order_id,amount`, values `1,12.5` and `2,25.0`) was profiled with the actual repository CLI:

```bash
shape profile /tmp/owner-notes2-vscode-project/orders.csv -o /tmp/owner-notes2-vscode-project/orders.shape
```

Exit **0**. The project uses the committed `editors/vscode/test/fixtures/valid.shape.yml` copied as `shape.yml`. The local fixture workspace was trusted. The temporary VS Code user setting `shape.path` points at the real `.venv/bin/shape`, not the fake CLI used by some unit tests. Telemetry/update settings were disabled in this temporary profile.

The [actual editor-check script](vscode-installed-editor-test.cjs) drove the installed app through its VS Code API. The repository's test-electron launcher used a separate empty `local-tests.receipt-harness` development extension only to enable the test host; it does not supply/override Shape. The script explicitly asserts that `sqllocks.shape` is loaded from `/tmp/owner-notes2-vscode-extensions/`, not `extensionDevelopmentPath`. It opens the real YAML and artifact through the installed custom editor, compares the full text to the real CLI's stdout, tests a typing command, and verifies the document is unchanged and not dirty.

Selected actual editor output (unrelated Electron/chat/settings diagnostics omitted; no check result substituted):

```text
PASS installed Shape VSIX: 0.1.0
VS Code 1.141.0
PASS real shape.yml opened as YAML
PASS .shape custom editor matches real shape cat; row_count 2; read-only
Exit code:   0
```

Editor host exit **0**; wrapper exit **0**. This is an installed-package receipt, not merely VSIX packaging or the prior CI development-extension run. It supplies the complete artifact requested by `docs/VSCODE.md:25`.

## Related

[Published receipts](index.md) · [Public owner decisions](../DECISIONS.md)
