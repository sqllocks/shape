// This file runs inside the real VS Code extension host launched by the command driver.
const assert = require('node:assert/strict');
const vscode = require('vscode');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {waitForDiff} = require('./docs_vscode_wait.cjs');
exports.run = async function () {
  const file = path.resolve(process.env.SHAPE_DOCS_EDITOR_FILE);
  const workspace = path.resolve(process.env.SHAPE_DOCS_EDITOR_WORKSPACE);
  const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(file));
  assert.ok(folder, 'The fixture artifact must belong to an open workspace');
  assert.equal(path.resolve(folder.uri.fsPath), workspace, 'Open the fixture git repository as the workspace');
  assert.equal(execFileSync('git', ['-C', workspace, 'rev-parse', '--show-toplevel'],
    {encoding: 'utf8'}).trim(), workspace, 'The workspace must be the fixture git repository');
  execFileSync('git', ['-C', workspace, 'cat-file', '-e', 'HEAD:demo.shape']);
  assert.ok(vscode.workspace.isTrusted, 'The fixture workspace must permit the real Shape CLI');
  const extension = vscode.extensions.getExtension('sqllocks.shape');
  assert.ok(extension, 'The built VSIX must be installed');
  const api = await extension.activate();
  await vscode.workspace.getConfiguration('shape').update('path',
    process.env.SHAPE_DOCS_EDITOR_SHAPE, vscode.ConfigurationTarget.Global);
  await vscode.commands.executeCommand('shape.compareWithHead', vscode.Uri.file(process.env.SHAPE_DOCS_EDITOR_FILE));
  const tab = await waitForDiff(vscode, file);
  assert.ok(tab instanceof vscode.TabInputTextDiff, 'Compare with Git HEAD must open a diff editor');
  const head = await vscode.workspace.openTextDocument(tab.original);
  const working = await vscode.workspace.openTextDocument(tab.modified);
  assert.notEqual(head.getText(), working.getText(), 'The real profiles must differ');
  assert.ok(head.getText().includes('3'), 'Committed profile has three rows');
  assert.ok(working.getText().includes('4'), 'Working profile has four rows');
  assert.deepEqual(api.reported, [], 'The installed extension must report no CLI or Git error');
  console.log('Installed sqllocks.shape: Shape: Compare with Git HEAD opened a diff for real 3-row and 4-row profiles; no errors');
};
