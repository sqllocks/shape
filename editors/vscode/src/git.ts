// The committed version of a file, through the `git` executable. No import of `vscode`.
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { execFile } from "child_process";

import { Exec, ShapeCli, catFile } from "./cli";

/** The default Exec: runs a program with no shell. */
export const nodeExec: Exec = (file, args) =>
  new Promise((resolve, reject) => {
    execFile(file, args, { maxBuffer: 256 * 1024 * 1024, encoding: "utf8" }, (err, stdout) => {
      if (err) {
        reject(err);
      } else {
        resolve({ stdout });
      }
    });
  });

function gitBytes(args: string[], cwd: string): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    execFile(
      "git",
      args,
      { cwd, maxBuffer: 1024 * 1024 * 1024, encoding: "buffer" },
      (err, stdout, stderr) => {
        if (err) {
          const detail = Buffer.from(stderr).toString("utf8").trim();
          reject(new Error(detail || `git ${args.join(" ")} failed`));
        } else {
          resolve(stdout);
        }
      },
    );
  });
}

/** The bytes of `file` as committed at HEAD. */
export async function headBytes(file: string): Promise<Buffer> {
  const dir = path.dirname(file);
  const top = (await gitBytes(["rev-parse", "--show-toplevel"], dir)).toString("utf8").trim();
  const rel = path.relative(fs.realpathSync(top), fs.realpathSync(file)).split(path.sep).join("/");
  return gitBytes(["show", `HEAD:${rel}`], dir);
}

/** The text form of `file` at HEAD: HEAD's bytes in a temporary file, through `shape cat`. */
export async function headTextForm(cli: ShapeCli, file: string, exec: Exec): Promise<string> {
  const bytes = await headBytes(file);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "shape-head-"));
  try {
    const copy = path.join(tmp, path.basename(file));
    fs.writeFileSync(copy, bytes);
    return await catFile(cli, copy, exec);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}
