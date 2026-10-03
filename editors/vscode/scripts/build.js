// Build: copy the schemas from src/shape/schemas (so the extension never carries a stale copy),
// compile the TypeScript, and stage exactly what ships into stage/ (the folder `vsce package`
// and the extension tests use).
"use strict";
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const root = path.resolve(__dirname, "..");
const schemaSource = path.resolve(root, "..", "..", "src", "shape", "schemas");
const SCHEMAS = [
  { file: "shape-project-v1.schema.json", required: true },
  // W1-06's generation spec schema: bundled when the repository has it, skipped with a message
  // otherwise (and its jsonValidation entry then left out of the packaged manifest).
  { file: "generation-spec-v1.schema.json", required: false },
];

function main() {
  fs.mkdirSync(path.join(root, "schemas"), { recursive: true });
  const bundled = new Set();
  for (const s of SCHEMAS) {
    const from = path.join(schemaSource, s.file);
    const to = path.join(root, "schemas", s.file);
    if (fs.existsSync(from)) {
      fs.copyFileSync(from, to);
      bundled.add(s.file);
    } else if (s.required) {
      throw new Error(`${from} is missing`);
    } else {
      fs.rmSync(to, { force: true });
      console.log(`shape-vscode: skipped ${s.file}: it is not in src/shape/schemas yet`);
    }
  }

  fs.rmSync(path.join(root, "out"), { recursive: true, force: true });
  execFileSync(process.execPath, [require.resolve("typescript/bin/tsc"), "-p", root], {
    stdio: "inherit",
  });

  const stage = path.join(root, "stage");
  fs.rmSync(stage, { recursive: true, force: true });
  fs.mkdirSync(stage, { recursive: true });
  const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  const contributes = pkg.contributes;
  contributes.jsonValidation = (contributes.jsonValidation || []).filter((j) =>
    bundled.has(path.basename(j.url)),
  );
  if (contributes.jsonValidation.length === 0) {
    delete contributes.jsonValidation;
  }
  delete pkg.scripts;
  delete pkg.devDependencies;
  pkg.main = "./out/src/extension.js";
  fs.writeFileSync(path.join(stage, "package.json"), JSON.stringify(pkg, null, 2) + "\n");
  fs.cpSync(path.join(root, "schemas"), path.join(stage, "schemas"), { recursive: true });
  fs.cpSync(path.join(root, "snippets"), path.join(stage, "snippets"), { recursive: true });
  const outSrc = path.join(root, "out", "src");
  fs.cpSync(outSrc, path.join(stage, "out", "src"), { recursive: true });
  for (const f of ["README.md", "LICENSE"]) {
    fs.copyFileSync(path.join(root, f), path.join(stage, f));
  }
  console.log(`shape-vscode: staged ${path.relative(process.cwd(), stage) || stage}`);
}

main();
