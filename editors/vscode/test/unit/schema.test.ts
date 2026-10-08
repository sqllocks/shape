import * as assert from "assert";
import Ajv2020 from "ajv/dist/2020";
import * as fs from "fs";
import * as path from "path";
import { parse } from "yaml";

const ROOT = path.resolve(__dirname, "..", "..", "..");
const FIXTURES = path.join(ROOT, "test", "fixtures");
const schema = JSON.parse(
  fs.readFileSync(path.join(ROOT, "schemas", "shape-project-v1.schema.json"), "utf8"),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
const validate = ajv.compile(schema);

function load(name: string): unknown {
  return parse(fs.readFileSync(path.join(FIXTURES, name), "utf8"));
}

describe("the bundled shape.yml schema", () => {
  it("accepts a valid shape.yml", () => {
    assert.ok(validate(load("valid.shape.yml")), JSON.stringify(validate.errors));
  });

  it("accepts the frozen version 1 project file of the Python package", () => {
    const frozen = path.join(ROOT, "..", "..", "tests", "fixtures", "project", "v1", "shape.yml");
    assert.ok(validate(parse(fs.readFileSync(frozen, "utf8"))), JSON.stringify(validate.errors));
  });

  it("rejects an invalid shape.yml and says where", () => {
    assert.strictEqual(validate(load("invalid.shape.yml")), false);
    const where = (validate.errors ?? []).map((e) => e.instancePath);
    assert.ok(where.some((p) => p.endsWith("/baseline/kind")), where.join(" "));
    assert.ok(where.some((p) => p.endsWith("/colour") || p === "/sources/orders"), where.join(" "));
    assert.ok(where.some((p) => p.endsWith("/distribution/mode")), where.join(" "));
  });

  it("rejects a file without sources or with a wrong format", () => {
    assert.strictEqual(validate({ format: "shape-project", version: 1 }), false);
    assert.strictEqual(validate({ format: "other", version: 1, sources: {} }), false);
  });
});
