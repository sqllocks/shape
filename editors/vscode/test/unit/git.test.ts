import * as assert from "assert";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { catFile } from "../../src/cli";
import { headBytes, headTextForm, nodeExec } from "../../src/git";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "test", "fixtures");
const CLI = { path: path.join(FIXTURES, "bin", "shape"), version: "0.9.0" };

function repo(): { dir: string; file: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shape-repo-"));
  const git = (...args: string[]) => execFileSync("git", args, { cwd: dir, stdio: "pipe" });
  git("init", "-q");
  git("config", "user.email", "t@example.com");
  git("config", "user.name", "t");
  fs.mkdirSync(path.join(dir, "shapes"));
  const file = path.join(dir, "shapes", "sample.shape");
  fs.copyFileSync(path.join(FIXTURES, "sample.shape"), file);
  git("add", "-A");
  git("commit", "-qm", "base");
  return { dir, file };
}

describe("the .shape text view with a fixture file", () => {
  it("shows the text form of the working copy", async () => {
    const { file } = repo();
    const text = await catFile(CLI, file, nodeExec);
    assert.strictEqual(
      text,
      'manifest.kind: "profile"\nsource: "sample.shape"\nbody: "rows=100"\n',
    );
  });

  it("shows the text form of HEAD, not of the working copy", async () => {
    const { file } = repo();
    fs.writeFileSync(file, "rows=250\n");
    assert.deepStrictEqual(await headBytes(file), Buffer.from("rows=100\n"));
    const head = await headTextForm(CLI, file, nodeExec);
    const work = await catFile(CLI, file, nodeExec);
    assert.ok(head.includes('"rows=100"') && !head.includes("250"));
    assert.ok(work.includes('"rows=250"'));
    assert.notStrictEqual(head, work);
  });

  it("keeps the file's name in the text of HEAD", async () => {
    const { file } = repo();
    assert.ok((await headTextForm(CLI, file, nodeExec)).includes('"sample.shape"'));
  });

  it("is an error for a file git does not know", async () => {
    const { dir } = repo();
    const loose = path.join(dir, "new.shape");
    fs.writeFileSync(loose, "x");
    await assert.rejects(headTextForm(CLI, loose, nodeExec), /HEAD|exist|path/i);
  });

  it("is an error outside a repository", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shape-norepo-"));
    const f = path.join(dir, "a.shape");
    fs.writeFileSync(f, "x");
    await assert.rejects(headBytes(f), /not a git repository|repository/i);
  });
});
