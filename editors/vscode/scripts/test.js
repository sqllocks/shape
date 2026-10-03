// `npm test`: build, the unit tests under node, then the extension tests in VS Code
// (@vscode/test-electron; on a machine without a display run it as `xvfb-run -a npm test`).
"use strict";
const path = require("path");
const { execFileSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const run = (args) => execFileSync(process.execPath, args, { cwd: root, stdio: "inherit" });

run([path.join(__dirname, "build.js")]);
run([require.resolve("mocha/bin/mocha.js"), "out/test/unit/*.test.js", "--timeout", "30000"]);
run([path.join(root, "out", "test", "runSuite.js")]);
