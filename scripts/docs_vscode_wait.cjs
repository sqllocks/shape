// Wait for VS Code's asynchronous editor presentation, without weakening the suite's assertion.
exports.waitForDiff = async function (vscode, file, timeoutMs = 10000, intervalMs = 100) {
  const deadline = Date.now() + timeoutMs;
  do {
    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        const input = tab.input;
        if (!(input instanceof vscode.TabInputTextDiff)
            || input.original.scheme !== 'shape-text' || input.modified.scheme !== 'shape-text') {
          continue;
        }
        try {
          if (JSON.parse(input.original.query).file === file
              && JSON.parse(input.modified.query).file === file) {
            return input;
          }
        } catch {
          // An unrelated or malformed URI is never evidence for this fixture.
        }
      }
    }
    if (Date.now() >= deadline) break;
    await new Promise(resolve => setTimeout(resolve, Math.min(intervalMs, deadline - Date.now())));
  } while (Date.now() < deadline);
  return undefined;
};
