import * as assert from "assert";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as vscode from "vscode";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "test", "fixtures");
const FAKE = path.join(FIXTURES, "bin", "shape");
const MIN = "0.9.0";

interface Api {
  textUri(file: string, rev: "working" | "head"): vscode.Uri;
  reported: string[];
}

async function setPath(value: string): Promise<void> {
  await vscode.workspace
    .getConfiguration("shape")
    .update("path", value, vscode.ConfigurationTarget.Global);
}

function repoWithShapeFile(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shape-ext-"));
  const git = (...a: string[]) => execFileSync("git", a, { cwd: dir, stdio: "pipe" });
  git("init", "-q");
  git("config", "user.email", "t@example.com");
  git("config", "user.name", "t");
  const file = path.join(dir, "sample.shape");
  fs.copyFileSync(path.join(FIXTURES, "sample.shape"), file);
  git("add", "-A");
  git("commit", "-qm", "base");
  fs.writeFileSync(file, "rows=250\n");
  return file;
}

describe("Shape extension", () => {
  let api: Api;
  let log: string;

  before(async () => {
    const ext = vscode.extensions.getExtension("sqllocks.shape");
    assert.ok(ext, "the extension is installed");
    api = (await ext.activate()) as Api;
  });

  beforeEach(async () => {
    log = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "shape-log-")), "calls.log");
    process.env.SHAPE_FAKE_LOG = log;
    delete process.env.SHAPE_FAKE_VERSION;
    await setPath(FAKE);
    api.reported.length = 0;
    await vscode.commands.executeCommand("workbench.action.closeAllEditors");
  });

  after(async () => {
    await setPath("");
  });

  it("opens a .shape file as read-only text from `shape cat`", async () => {
    const file = repoWithShapeFile();
    const doc = await vscode.workspace.openTextDocument(api.textUri(file, "working"));
    assert.strictEqual(doc.uri.scheme, "shape-text");
    assert.strictEqual(
      doc.getText(),
      'manifest.kind: "profile"\nsource: "sample.shape"\nbody: "rows=250"\n',
    );
    // typing into the editor changes nothing: documents of a content provider are read-only
    await vscode.window.showTextDocument(doc);
    await vscode.commands.executeCommand("type", { text: "x" });
    assert.ok(!doc.getText().startsWith("x"), "the view is read-only");
    assert.strictEqual(doc.isDirty, false);
  });

  it("opens a real .shape file in the text view through the custom editor", async () => {
    const file = repoWithShapeFile();
    await vscode.commands.executeCommand("vscode.open", vscode.Uri.file(file));
    for (let i = 0; i < 50; i++) {
      const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
      if (input instanceof vscode.TabInputText && input.uri.scheme === "shape-text") {
        return;
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.fail("the text form did not open");
  });

  it("Shape: Compare with Git HEAD diffs the two text forms", async () => {
    const file = repoWithShapeFile();
    await vscode.commands.executeCommand("shape.compareWithHead", vscode.Uri.file(file));
    const input = vscode.window.tabGroups.activeTabGroup.activeTab?.input;
    assert.ok(input instanceof vscode.TabInputTextDiff, "a diff editor is open");
    const original = await vscode.workspace.openTextDocument(input.original);
    const modified = await vscode.workspace.openTextDocument(input.modified);
    assert.ok(original.getText().includes('"rows=100"'));
    assert.ok(modified.getText().includes('"rows=250"'));
  });

  it("a missing shape executable: one error naming the setting and version, nothing run", async () => {
    await setPath(path.join(os.tmpdir(), "no-such-dir", "shape"));
    const file = repoWithShapeFile();
    await vscode.commands.executeCommand("shape.compareWithHead", vscode.Uri.file(file));
    await vscode.commands.executeCommand("shape.compareWithHead", vscode.Uri.file(file));
    assert.strictEqual(api.reported.length, 1, api.reported.join("\n"));
    assert.ok(api.reported[0].includes("shape.path") && api.reported[0].includes(MIN));
    assert.strictEqual(fs.existsSync(log), false, "no shape command was run");
    assert.ok(
      !(vscode.window.tabGroups.activeTabGroup.activeTab?.input instanceof vscode.TabInputTextDiff),
    );
  });

  it("a too old shape: one error, and only --version was run", async () => {
    process.env.SHAPE_FAKE_VERSION = "0.1.0";
    await setPath(FAKE + " "); // a changed value re-checks
    const file = repoWithShapeFile();
    await vscode.commands.executeCommand("shape.compareWithHead", vscode.Uri.file(file));
    await vscode.commands.executeCommand("shape.compareWithHead", vscode.Uri.file(file));
    assert.strictEqual(api.reported.length, 1);
    assert.ok(api.reported[0].includes("too old") && api.reported[0].includes(MIN));
    assert.strictEqual(fs.readFileSync(log, "utf8"), "--version\n");
  });
});
