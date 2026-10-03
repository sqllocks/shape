# Shape in CI: reports, exit codes, `--json` and `--dry-run`

The checking commands write the two reports CI systems read, every command ends with a
documented exit code, and every command can print one JSON document or say what it would do before
it does it.

| You want | Use |
|---|---|
| a test report in the pull request | `--junit FILE` on `diff`, `check`, `verify`, `fidelity`, `profile validate --safe` |
| findings in code scanning | `--sarif FILE` on the same commands |
| the result in a script | `--json` (one `shape-result` document on standard output) |
| to see what a command would write | `--dry-run` |
| to know what an exit code means | [`docs/EXIT_CODES.md`](EXIT_CODES.md), or the end of `--help` |

Writing a report never changes a command's exit code: a failing `diff --junit` still exits as
`diff` does. A report that cannot be written (a path below a file, a read-only folder) is one
warning on standard error.

## Project defaults: the `ci:` block

`shape.yml` can name the report paths, so the command lines in CI stay short. Each path may contain
`{command}` (`diff`, `check`, `verify`, `fidelity`, `profile-validate`). A flag on the command line
wins over the file, and `--no-project` ignores the file. Relative paths are relative to the folder
that holds `shape.yml`.

```yaml
ci:
  junit: reports/{command}.xml
  sarif: reports/{command}.sarif
  json: reports/{command}.json     # the shape-result document, for commands that print one
```

The block is an additive key of `shape-project` version 1 (see `docs/PROJECT.md`), so older files
read as before. A path without `{command}` is shared by every command that writes it: the last one
wins.

## JUnit

`--junit FILE` writes one `<testsuites name="shape">` holding one `<testsuite name="shape COMMAND">`
with the attributes `tests`, `failures`, `errors`, `skipped` and `time`. There is one
`<testcase classname="TABLE" name="COLUMN:CHECK">` for every check the command evaluated, passing
or not (a drift kind, a contract rule, a validation gate, a fidelity score); the counts in the
attributes equal the elements.

```xml
<testsuites name="shape">
  <testsuite name="shape diff" tests="5" failures="3" errors="0" skipped="0" time="0.337">
    <testcase classname="b" name="amt:mean_shift" time="0">
      <failure type="mean_shift" message="mean_shift on b.amt, severity medium, score 0.9131, baseline 10.055, current 29.8872" />
    </testcase>
    <testcase classname="b" name="id:drift" time="0" />
  </testsuite>
</testsuites>
```

- A failing check has `<failure type="CHECK" message="...">`.
- A gate of `shape verify` that fails while `shape.yml` sets it to `observe` is
  `<skipped message="observe mode: ...">`: the report shows it, the build does not fail.
- A change that a planned-change entry covers (the planned-change registry) passes and carries a
  `<property name="planned" value="ID">`.
- `diff` has a passing `COLUMN:drift` case for every column with no change, `check` one passing case
  for every contract rule that held, `fidelity` one `score` case for the whole, each table and each
  column (failing under the pass marks of `docs/FIDELITY.md`), `profile validate --safe` one
  `no_leaks` case when the scan is clean.

### What the messages never contain

A message names the rule, the place (`TABLE.COLUMN`) and, for rules about counts and rates, the
numbers. It never contains a value found in the data: not an allowed value that was not allowed,
not a minimum or maximum, not the string a leak scan found, and not the free text of a validation
gate (which can quote a value; the gate's message count is in the report, its text is in
`shape verify -o REPORT`). Values that the safe capture suppresses are never in a message.

## SARIF

`--sarif FILE` writes a SARIF 2.1.0 log (`$schema` and `version: "2.1.0"`) with one run:

- `tool.driver` has `name: "shape"`, `version` (Shape's version), `informationUri` and `rules`: one
  `reportingDescriptor` (`id`, `shortDescription`, `helpUri` to the documentation) for every drift
  kind, contract rule or gate that produced a result.
- Each result is a failing check (or, for a gate in observe mode, a `note`): `ruleId`, `level`
  (`high` to `error`, `medium` to `warning`, `low` to `note`), `message.text`, a
  `physicalLocation` whose `artifactLocation.uri` is the input path relative to the working
  directory, a `logicalLocation` with `fullyQualifiedName` `TABLE.COLUMN`, and
  `partialFingerprints["shapeFinding/v1"]`.
- The input is the current profile for `diff`, the contract for `check`, the data path for
  `verify`, the synthetic data for `fidelity` and the artifact for `profile validate --safe`.
- The fingerprint is the sha256 of the rule id, a line feed and `TABLE.COLUMN`: the same finding
  keeps its identity across runs, so code scanning tracks it instead of reporting it as new.

```json
{
  "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
  "version": "2.1.0",
  "runs": [{
    "tool": {"driver": {
      "name": "shape", "version": "0.9.0", "informationUri": "https://github.com/sqllocks/shape",
      "rules": [{"id": "mean_shift", "shortDescription": {"text": "diff: mean_shift"},
                 "helpUri": "https://github.com/sqllocks/shape/blob/main/docs/DRIFT.md#what-is-compared"}]
    }},
    "results": [{
      "ruleId": "mean_shift", "level": "warning",
      "message": {"text": "mean_shift on b.amt, severity medium, score 0.9131, baseline 10.055, current 29.8872"},
      "locations": [{"physicalLocation": {"artifactLocation": {"uri": "b.shape"}},
                     "logicalLocations": [{"fullyQualifiedName": "b.amt"}]}],
      "partialFingerprints": {"shapeFinding/v1": "d9f22f03ba150ca72e6ecabc86cfdefd8d28441034eb2fe047fb6978e5d24469"}
    }]
  }]
}
```

## Contract rules

`shape check` evaluates the rules of a contract (`docs/DESIGN.md`, `docs/JOINT.md`): the row count
bounds (`row_count.min`, `row_count.max`), `required_column`, `column_exists`, and per column
`dtype`, `nullable`, `unique`, `max_null_rate`, `pattern`, `distribution`, `allowed_values`,
`min`, `max`, `min_true_rate`, `max_true_rate`, `no_placeholder` and the joint rules. Each is the
`CHECK` of a test case and the `ruleId` of a SARIF result.

## `--json`: one document on standard output

With `--json` a command prints exactly one JSON document on standard output and sends its text to
standard error, so `shape ... --json | jq` works:

```json
{"passed": false, "violations": [{"column": "zz", "expected": "present", "observed": "missing", "rule": "column_exists"}],
 "format": "shape-result", "version": 1, "command": "check", "exit_code": 1}
```

- The envelope keys are `format` (`shape-result`), `version` (`1`), `command` (`"registry commit"`)
  and `exit_code`. The keys of what the command prints are kept, unchanged, next to them.
- A command that prints text prints it under `output` (and on standard error); one that prints a
  list prints it under `payload`; `error` holds the one-line message of an expected error (exit 2).
- A command whose payload has a key named like an envelope key (`generate` prints a `format`) has
  the whole payload under `payload` as well; the envelope's keys win at the top level.
- `profile`, `diff`, `check` and `design` take a file for `--json` and keep that meaning: the file is
  the command's own result, as before. `--json -` writes the `shape-result` document to standard
  output instead.
- `shape version` and `shape bridge` (a server) have no `--json`.

## `--dry-run`: what would this do?

`--dry-run` is on every core command that writes files, writes to a target (`--to`, `--sink`) or
changes a registry or project file. It reads and checks the inputs, resolves the targets without
opening a connection or signing in, prints the planned actions, writes nothing and exits 0, or 2
for an input the command would refuse:

```
$ shape diff a.shape b.shape --junit reports/diff.xml --dry-run
would create reports/diff.xml
$ shape emit retail --sink kafka://user:secret@broker:9092/topic --dry-run --json
{"actions": [{"action": "send", "target": "kafka://broker:9092/topic"}], "command": "emit", "format": "shape-dry-run", "version": 1}
```

- `actions` is a list of `{"action": "write" | "create" | "delete" | "send", "target": ...}`:
  `create` for a file that does not exist, `write` for one that does, `delete` for something
  removed, `send` for a sink or a service. Credentials are removed from targets.
- What is checked: the arguments, that every input exists, that an output is not below a file, and
  for `init`, `keygen`, `git-setup`, `jobs`, `profile registry` and `proposals` the state they need
  (a name that is free, a job that exists, a git repository). Reading the content of an input is
  left to the command itself.
- `shape generate`, `shape demo run` and `shape demo cleanup` keep the `--dry-run` they always had
  (a plan, with exit 1 when the plan has problems).

## A GitHub Actions workflow

This job checks a profile of the data against the committed baseline, uploads the findings to code
scanning and publishes the test report in the pull request. The command's own exit code decides the
job; the two steps after it run in any case.

```yaml
name: shape
on: [pull_request]
permissions:
  contents: read
  security-events: write
  checks: write
  pull-requests: write
jobs:
  shape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install "sqllocks-shape[yaml]"
      - run: shape profile orders -o current.shape
      - run: shape diff current.shape --fail-on-drift --junit reports/diff.xml --sarif reports/diff.sarif
      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with: {sarif_file: reports/diff.sarif}
      - uses: dorny/test-reporter@v1
        if: always()
        with:
          name: shape diff
          path: reports/diff.xml
          reporter: java-junit
```

With the `ci:` block in `shape.yml` the two flags are not needed. `shape init` writes a smaller
workflow to start from (`docs/PROJECT.md`).

## Exit codes

Every command's codes are in [`EXIT_CODES.md`](EXIT_CODES.md), generated from
`shape.cli.exitcodes`; `python scripts/gen_exit_codes.py --check` fails when it is out of date
and runs in `make check`. In short: 0 ok, 1 a check failed, 2 bad input, 3 and above a command's
own verdict.
