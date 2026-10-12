const assert = require('node:assert/strict');
const test = require('node:test');
const {waitForDiff} = require('../../scripts/docs_vscode_wait.cjs');
class TabInputTextDiff {
  constructor(file) {
    this.original = {scheme: 'shape-text', query: JSON.stringify({file, rev: 'head'})};
    this.modified = {scheme: 'shape-text', query: JSON.stringify({file, rev: 'working'})};
  }
}
const file = '/fixture/demo.shape';
function editor(groups) {
  return {TabInputTextDiff, window: {tabGroups: {all: groups}}};
}

test('finds the matching diff in an inactive group', async () => {
  const wanted = new TabInputTextDiff(file);
  const vscode = editor([{tabs: [{input: {}}]}, {tabs: [{input: wanted}]}]);
  assert.equal(await waitForDiff(vscode, file, 100, 1), wanted);
});

test('waits for a diff that appears after command completion', async () => {
  const group = {tabs: []};
  const wanted = new TabInputTextDiff(file);
  setTimeout(() => { group.tabs.push({input: wanted}); }, 20);
  assert.equal(await waitForDiff(editor([group]), file, 1000, 1), wanted);
});

test('timeout preserves the failure of the original assertion', async () => {
  const tab = await waitForDiff(editor([{tabs: []}]), file, 10, 1);
  assert.equal(tab, undefined);
  assert.throws(() => assert.ok(tab instanceof TabInputTextDiff,
    'Compare with Git HEAD must open a diff editor'), /Compare with Git HEAD/);
});

test('unrelated diff tabs cannot satisfy the fixture', async () => {
  const other = new TabInputTextDiff('/fixture/other.shape');
  assert.equal(await waitForDiff(editor([{tabs: [{input: other}]}]), file, 10, 1), undefined);
});

test('malformed or non-Shape diff tabs are ignored', async () => {
  const malformed = new TabInputTextDiff(file);
  malformed.modified.query = 'not JSON';
  const foreign = new TabInputTextDiff(file);
  foreign.modified.scheme = 'file';
  assert.equal(await waitForDiff(editor([{tabs: [{input: malformed}, {input: foreign}]}]),
    file, 10, 1), undefined);
});
