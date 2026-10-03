import * as path from "path";
import Mocha from "mocha";
import * as fs from "fs";

export function run(): Promise<void> {
  const mocha = new Mocha({ ui: "bdd", timeout: 30000, color: true });
  for (const f of fs.readdirSync(__dirname).filter((n) => n.endsWith(".test.js"))) {
    mocha.addFile(path.join(__dirname, f));
  }
  return new Promise((resolve, reject) => {
    mocha.run((failures) =>
      failures > 0 ? reject(new Error(`${failures} tests failed`)) : resolve(),
    );
  });
}
