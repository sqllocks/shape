# HUNT2-plugins: second-pass bug hunt of the plugin framework

Branch `lane/HUNT2-plugins` (from `origin/int/INT-17`). Area: `src/shape/plugins/**`, the plugin
kit, `plugins/*/` glue and their tests. The first audit (`lane/AUD-pluginfw`, issues #366-#382) was
read and none of its findings was re-filed; note that lane is not merged into INT-17, and its edits
to `plugins/cli.py`, `kit.py` and `schemes.py` may conflict textually with this lane's edits to
`plugins/cli.py` and `kit.py` (different hunks). No gate, tolerance or D-xx/T-xx decision changed;
no test was skipped, xfailed or weakened.

## Findings

| # | sev | issue | defect | fix |
|---|---|---|---|---|
| 1 | high | #579 | a line break in a RECORD path makes two different file lists share one signed message | 084b334a |
| 2 | medium | #580 | malformed RECORD (`_csv.Error`, `shake_*` hash) escapes host discovery, verify and sign | 084b334a |
| 3 | medium | #581 | `plugins sign` rewrites RECORD without CSV quoting (comma in a name breaks it) | 084b334a |
| 4 | medium | #582 | a signed wheel is left with mode 0600 | 084b334a |
| 5 | medium | #583 | `plugins.allowlist` in `shape.yml` is documented but rejected by the schema and never read | open (outside area) |
| 6 | medium | #584 | kit reports NaN-returning plugins as "not deterministic" | see git log (fix #584) |
| 7 | low | #585 | `check_behavior` TypeError on null ids; `check_chaos` accepts a bool | same |
| 8 | low | #586 | `--debug` ignored when a plugin command crashes | same |
| 9 | low | #587 | `plugins info`/`list --group` reject the short group form used by the allow-list | same |
| 10 | low | #588 | signature version < 1 accepted; `sign -o` error names a temp file | 084b334a |

Regression tests: `tests/plugins/test_hunt2_plugins.py` (commit 9beed1f5 holds the failing run:
22 failed, 1 passed; all pass after the fixes). Negative and boundary cases: spaces and unicode in a
path still sign; bool and negative `rows_affected`; unknown short group stays unknown.

## Open items for the lead

- #583: needs a `plugins` key in `shape-project-v1.schema.json` (project code, outside this area), the
  project file passed to `default_host()`, and a decision on resolving the relative path against the
  project file. Not changed here.
- `shape plugins sign --key k.pub` signs with a public key file as if it were private: raw base64
  private and public key files are indistinguishable (legacy key format in `shape.artifact.keys`).
  Not filed as a defect; a lead decision.
- Signed list covers only files RECORD hashes: a file not listed in RECORD is outside the signature
  (documented scope of the check; not changed).
- Equivalence verifiers: none compares bytes this lane changed (no generation, profile or sink code
  was touched), so none was run.

## Commands and results

See the final section appended below once the full runs finish.
