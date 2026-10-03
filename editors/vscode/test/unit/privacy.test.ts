import * as assert from "assert";
import * as fs from "fs";
import * as path from "path";

const SRC = path.resolve(__dirname, "..", "..", "..", "src");
const MANIFEST = path.resolve(__dirname, "..", "..", "..", "package.json");

// Modules and calls that open a connection or report usage. The only child processes the
// extension starts are `shape` and `git`.
const FORBIDDEN = [
  /from\s+["'](?:node:)?(?:http|https|http2|net|tls|dgram|dns|worker_threads|cluster)["']/,
  /require\(\s*["'](?:node:)?(?:http|https|http2|net|tls|dgram|dns)["']\s*\)/,
  /\bfetch\s*\(/,
  /XMLHttpRequest|WebSocket|navigator\.sendBeacon/,
  /telemetry|applicationinsights|@vscode\/extension-telemetry|vscode\.env\.createTelemetryLogger/i,
];

describe("no telemetry and no network", () => {
  const files = fs.readdirSync(SRC).filter((f) => f.endsWith(".ts"));
  it("has source files to check", () => {
    assert.ok(files.length >= 3);
  });
  for (const f of files) {
    it(`${f} opens no connection and reports no usage`, () => {
      const text = fs.readFileSync(path.join(SRC, f), "utf8");
      // the header comment states the promise in words; look at code only
      const code = text.replace(/\/\/.*$/gm, "");
      for (const pattern of FORBIDDEN) {
        assert.ok(!pattern.test(code), `${f} matches ${pattern}`);
      }
    });
  }
  it("declares no runtime dependency", () => {
    const pkg = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
    assert.strictEqual(pkg.dependencies, undefined);
  });
  it("runs only shape and git", () => {
    const text = fs.readFileSync(path.join(SRC, "git.ts"), "utf8") + fs.readFileSync(path.join(SRC, "cli.ts"), "utf8");
    const spawned = [...text.matchAll(/execFile\(\s*"([^"]+)"/g)].map((m) => m[1]);
    assert.deepStrictEqual([...new Set(spawned)].sort(), ["git"]);
  });
});
