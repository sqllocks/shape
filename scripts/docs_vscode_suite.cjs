// This file runs inside the real VS Code extension host launched by the command driver.
const assert = require('node:assert/strict');
const vscode = require('vscode');
exports.run = async function () {
  const extension = vscode.extensions.getExtension('sqllocks.shape');
  assert.ok(extension, 'The built VSIX must be installed');
  const api = await extension.activate();
  await vscode.commands.executeCommand('shape.compareWithHead', vscode.Uri.file(process.env.SHAPE_DOCS_EDITOR_FILE));
  const tab = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
  assert.ok(tab instanceof vscode.TabInputTextDiff, 'Compare with Git HEAD must open a diff editor');
  const head = await vscode.workspace.openTextDocument(tab.original);
  const working = await vscode.workspace.openTextDocument(tab.modified);
  assert.notEqual(head.getText(), working.getText(), 'The real profiles must differ');
  assert.ok(head.getText().includes('3'), 'Committed profile has three rows');
  assert.ok(working.getText().includes('4'), 'Working profile has four rows');
  assert.deepEqual(api.reported, [], 'The installed extension must report no CLI or Git error');
  console.log('Installed sqllocks.shape: Shape: Compare with Git HEAD opened a diff for real 3-row and 4-row profiles; no errors');
};
