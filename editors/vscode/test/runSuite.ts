// Starts VS Code (downloaded once by @vscode/test-electron) with the staged extension and runs
// the suite in test/suite. Under CI: `xvfb-run -a npm test`.
import { spawnSync } from "child_process";
import * as path from "path";
import {
  downloadAndUnzipVSCode,
  resolveCliArgsFromVSCodeExecutablePath,
  runTests,
} from "@vscode/test-electron";

async function main(): Promise<void> {
  const root = path.resolve(__dirname, "..", "..");
  const vscodeExecutablePath = await downloadAndUnzipVSCode();
  // The extension depends on the Red Hat YAML extension (it does the completion and validation):
  // install it into the test instance, as a user would.
  const [cli, ...args] = resolveCliArgsFromVSCodeExecutablePath(vscodeExecutablePath);
  const installed = spawnSync(cli, [...args, "--install-extension", "redhat.vscode-yaml"], {
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  process.stdout.write(installed.stdout ?? "");
  process.stderr.write(installed.stderr ?? "");
  if (installed.status !== 0) {
    throw new Error("could not install redhat.vscode-yaml into the test instance");
  }
  await runTests({
    vscodeExecutablePath,
    extensionDevelopmentPath: path.join(root, "stage"),
    extensionTestsPath: path.join(__dirname, "suite", "index"),
    launchArgs: ["--disable-workspace-trust"],
  });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
