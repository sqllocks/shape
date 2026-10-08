import * as assert from "assert";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import {
  Exec,
  MIN_VERSION,
  ShapeUnavailableError,
  catFile,
  checkShape,
  compareVersions,
  locateShape,
  parseVersion,
} from "../../src/cli";

const FIXTURES = path.resolve(__dirname, "..", "..", "..", "test", "fixtures");
const FAKE = path.join(FIXTURES, "bin", "shape");

function recorder(version: string): { exec: Exec; calls: string[][] } {
  const calls: string[][] = [];
  const exec: Exec = async (file, args) => {
    calls.push([file, ...args]);
    return { stdout: args[0] === "--version" ? `shape ${version}\n` : "text\n" };
  };
  return { exec, calls };
}

describe("versions", () => {
  it("parses and compares", () => {
    assert.deepStrictEqual(parseVersion("shape 0.9.12"), [0, 9, 12]);
    assert.strictEqual(parseVersion("no version here"), null);
    assert.strictEqual(compareVersions([0, 9, 0], [0, 9, 0]), 0);
    assert.strictEqual(compareVersions([0, 10, 0], [0, 9, 9]), 1);
    assert.strictEqual(compareVersions([0, 8, 99], [0, 9, 0]), -1);
  });
});

describe("locateShape", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shape-path-"));
  const exe = path.join(dir, "shape");
  fs.writeFileSync(exe, "");
  it("finds shape on PATH", () => {
    assert.strictEqual(locateShape("", { PATH: `/nonexistent${path.delimiter}${dir}` }), exe);
  });
  it("prefers the setting over PATH", () => {
    assert.strictEqual(locateShape(FAKE, { PATH: dir }), FAKE);
  });
  it("does not fall back to PATH when the setting names a missing file", () => {
    assert.strictEqual(locateShape(path.join(dir, "nope"), { PATH: dir }), null);
  });
  it("returns null with nothing on PATH", () => {
    assert.strictEqual(locateShape("", { PATH: "/nonexistent" }), null);
    assert.strictEqual(locateShape("", {}), null);
  });
  it("tries the executable extensions on Windows", () => {
    const names: string[] = [];
    locateShape("", { PATH: "bin", PATHEXT: ".EXE;.CMD" }, "win32", (p) => {
      names.push(path.basename(p));
      return false;
    });
    assert.deepStrictEqual(names, ["shape.exe", "shape.cmd"]);
  });
});

describe("checkShape", () => {
  it("accepts the minimum version and newer", async () => {
    for (const v of [MIN_VERSION, "0.9.1", "1.2.0"]) {
      const { exec } = recorder(v);
      const cli = await checkShape(FAKE, exec);
      assert.strictEqual(cli.version, v);
    }
  });

  it("a missing executable is one error naming the setting and the minimum version", async () => {
    const { exec, calls } = recorder("0.9.0");
    await assert.rejects(checkShape("/nonexistent/shape", exec), (err: Error) => {
      assert.ok(err instanceof ShapeUnavailableError);
      assert.ok(err.message.includes("shape.path"), err.message);
      assert.ok(err.message.includes(MIN_VERSION), err.message);
      return true;
    });
    assert.deepStrictEqual(calls, [], "nothing was run");
  });

  it("a too old executable is one error naming the setting and the minimum version", async () => {
    const { exec, calls } = recorder("0.8.9");
    await assert.rejects(checkShape(FAKE, exec), (err: Error) => {
      assert.ok(err instanceof ShapeUnavailableError);
      assert.ok(err.message.includes("0.8.9") && err.message.includes("too old"), err.message);
      assert.ok(err.message.includes("shape.path") && err.message.includes(MIN_VERSION));
      return true;
    });
    assert.deepStrictEqual(calls, [[FAKE, "--version"]], "only the version was asked");
  });

  it("an executable that fails or prints no version is not usable", async () => {
    const failing: Exec = async () => {
      throw new Error("boom");
    };
    await assert.rejects(checkShape(FAKE, failing), ShapeUnavailableError);
    const mute: Exec = async () => ({ stdout: "hello" });
    await assert.rejects(checkShape(FAKE, mute), ShapeUnavailableError);
  });
});

describe("catFile", () => {
  it("runs `shape cat FILE`", async () => {
    const { exec, calls } = recorder("0.9.0");
    const text = await catFile({ path: FAKE, version: "0.9.0" }, "x.shape", exec);
    assert.strictEqual(text, "text\n");
    assert.deepStrictEqual(calls, [[FAKE, "cat", "x.shape"]]);
  });
});
