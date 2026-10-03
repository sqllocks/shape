// Finding and checking the `shape` executable, and running `shape cat`. No import of `vscode`:
// everything here is tested under plain node.
import * as fs from "fs";
import * as path from "path";

/** The oldest `shape` the extension works with (the first with `shape cat`). */
export const MIN_VERSION = "0.9.0";

export interface ExecResult {
  stdout: string;
}
/** Runs a program with arguments; rejects when it cannot be started or exits non-zero. */
export type Exec = (file: string, args: string[]) => Promise<ExecResult>;

/** The one error for a missing or too old `shape`; its message names the setting and version. */
export class ShapeUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ShapeUnavailableError";
  }
}

export function parseVersion(text: string): number[] | null {
  const m = /(\d+)\.(\d+)\.(\d+)/.exec(text);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

export function compareVersions(a: number[], b: number[]): number {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) {
      return a[i] < b[i] ? -1 : 1;
    }
  }
  return 0;
}

function isFile(p: string): boolean {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

/** `configured` when it names a file, else the first `shape` on `PATH`; null when none. */
export function locateShape(
  configured: string,
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  exists: (p: string) => boolean = isFile,
): string | null {
  const given = configured.trim();
  if (given) {
    return exists(given) ? given : null;
  }
  const names =
    platform === "win32"
      ? (env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";").map((e) => `shape${e.toLowerCase()}`)
      : ["shape"];
  for (const dir of (env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (exists(candidate)) {
        return candidate;
      }
    }
  }
  return null;
}

export const NOT_FOUND =
  `The Shape command line was not found. Install it (pip install sqllocks-shape) or set the ` +
  `"shape.path" setting to the shape executable. Version ${MIN_VERSION} or newer is required.`;

export function tooOld(found: string, where: string): string {
  return (
    `The Shape command line at ${where} is version ${found}, which is too old. Upgrade it, or ` +
    `set the "shape.path" setting to a newer shape executable. Version ${MIN_VERSION} or newer ` +
    `is required.`
  );
}

export interface ShapeCli {
  path: string;
  version: string;
}

/** Locates `shape` and checks its version with `shape --version`; throws ShapeUnavailableError. */
export async function checkShape(
  configured: string,
  exec: Exec,
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  exists: (p: string) => boolean = isFile,
): Promise<ShapeCli> {
  const found = locateShape(configured, env, platform, exists);
  if (found === null) {
    throw new ShapeUnavailableError(NOT_FOUND);
  }
  let output: string;
  try {
    output = (await exec(found, ["--version"])).stdout;
  } catch {
    throw new ShapeUnavailableError(NOT_FOUND);
  }
  const version = parseVersion(output);
  if (version === null) {
    throw new ShapeUnavailableError(NOT_FOUND);
  }
  if (compareVersions(version, parseVersion(MIN_VERSION) as number[]) < 0) {
    throw new ShapeUnavailableError(tooOld(version.join("."), found));
  }
  return { path: found, version: version.join(".") };
}

/** The text form of a `.shape` file: the output of `shape cat FILE`. */
export async function catFile(cli: ShapeCli, file: string, exec: Exec): Promise<string> {
  return (await exec(cli.path, ["cat", file])).stdout;
}
