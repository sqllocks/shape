# W-pdb-1 review

Verdict: **APPROVE**

- Lane: `lane/W-pdb-1`, head reviewed `96cf893ce8c6495277be0b1a7118b53b6677643a` (confirmed equal to
  `origin/lane/W-pdb-1`).
- Base: `origin/main` = `c10923be9278bc63f2713ac0ed32a548b59a7afb`.
- Scope: `git diff origin/main...HEAD` (52e6fb0 implementation, 96cf893 extra test and status file;
  4644c72 and 164b834 are status-only blocker notes).
- Task: plan `PROMOTION_PLAN_0.9.3.md` Decision 13 and section 4.11 row `profile-db` item (a).
- Reviewer is independent of both builders. No code was changed by this review.

## Commands and exit codes

Environment: `python3.11 -m venv .venv`, `make bootstrap` (exit 0), then the five first-party
plugins installed editable (exit 0).

| Command | Exit | Result |
|---|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | 0 | clean |
| `ruff format --check src tests plugins benchmarks/vs_refengine` | 0 | 2080 files already formatted |
| `mypy` | 0 | no issues in 698 source files |
| `python scripts/cli_surface.py --check` | 0 | 0 breaking changes, 0 notes |
| `python scripts/cli_surface.py --check --plugins` | n/a | option does not exist on this base (the script states plugin commands are not in the snapshot) |
| `pytest -q plugins/shape-sqlserver/tests -p no:cov --ignore plugins/shape-sqlserver/tests/test_emulator.py` | 0 | 174 passed, 4 existing warnings |
| `pytest -q -p no:cov tests/docs/test_reference_examples.py -k sqlserver` (changed page) | 0 | 1 passed |
| Red check: new test file run against `origin/main`'s `command.py` (file swapped in, then restored with `git checkout`) | 1 | all 8 new cases fail on the base, so the red evidence is genuine, including the 96cf893 unresolvable-reference test |
| `git diff origin/main...HEAD` scanned for skip / xfail / gate / tolerance changes | n/a | none; only `parametrize` and a `contract` marker added |
| CHANGELOG placement | n/a | one entry, under `## Unreleased` only (replaces "No unreleased changes.") |

Not counted, per the lead: the full core-stage coverage figure and the docs / docs_site failures
that also fail on a clean `origin/main` in this sandbox. The full suite is proven in GitHub CI.

## Security probes (real `shape` CLI, synthetic values only)

Every run below exited 2 with exactly one `shape: error:` line; none printed the synthetic value
held in the referenced environment variable or the literal passed on the command line.

| Input to `--client-secret` | Message |
|---|---|
| a literal | `--client-secret takes a credential reference (env://, file:// or kv://)` |
| a literal, with global `--debug` and with `SHAPE_DEBUG=1` | same single line, no traceback (the plugin's `run` catches the error before the global debug path) |
| a literal, with `--log-json --metrics FILE` | same line; log lines and the metrics file record only the command name and exit code (metrics file grep for the literal: 0 matches) |
| empty (`--client-secret=`) | refused as a literal |
| `env://` | `env:// needs a variable name, as env://NAME` |
| `ENV://NAME` (uppercase), ` env://NAME` (leading space), `envVALUE` | refused as a literal, value not echoed |
| `env://NAME ` (trailing space) | names the reference `env://NAME ` only |
| `env://MISSING` | `environment variable MISSING is not set (env://MISSING)` |
| `file://`, missing file, group-readable file (0644) | path-only messages; 0644 is refused with the chmod hint |
| `kv://`, `kv://X`, `kv://vault/name` | reference-only messages from the Fabric Key Vault resolver |

Other leak paths checked by reading code:

- The `args` namespace only ever holds the reference; the resolved value lives in a local and in
  `Credentials`, whose `__repr__` masks `client_secret` (`plugins/shape-sqlserver/src/shape_sqlserver/auth.py:43`).
- `credrefs.resolve_reference` raises only `CredentialReferenceError` (a `ValueError`) and raises a
  store failure outside the `except` block, so there is no chained exception holding the original
  text; `ProfileDbCommand.run` catches `ValueError` and passes the text through
  `redact_connection_string`.
- Help text shows no default value (`metavar="REF"`, no `default=`).
- The run log (`_logged` in `src/shape/cli/main.py`) records the command name, not argv.

Reuse: the value is resolved by `shape.security.credrefs.resolve_reference`, the same function
publish reaches through `shape_fabric.auth.resolve_secret`. Same schemes, same `file://` privacy
rule, same `kv://` discovery of `shape_fabric.keyvault` and the same `register_resolver` override.
No new resolver was added.

Every other `profile-db` flag is unchanged (diff touches only the `--client-secret` argument and
the secret selection in `_run`); `cli_surface.py --check` reports no change to the core surface.

## Findings

No blocking findings.

Non-blocking:

1. `plugins/shape-sqlserver/src/shape_sqlserver/command.py:89`: the literal gate is a hard-coded
   `startswith(("env://", "file://", "kv://"))`, whereas publish's gate is `credrefs.is_reference`
   (`src/shape/cli/auth.py:75`, `_check_secret`). The resolver is shared, so behaviour is the same
   for the three schemes; the only difference is a host-registered custom scheme, which publish
   accepts and `profile-db` refuses. That matches Decision 13's "env, file or kv only", so it is
   acceptable. If the lead wants exact parity with publish, call `credrefs.is_reference` (or
   `credrefs.scheme_of(...) in {"env", "file", "kv"}`) instead of the literal tuple.
2. `command.py:87-93`: the reference is resolved before the cheap argument checks
   (`--sample-rows`, `-o`/`--json`). A `kv://` reference therefore costs a Key Vault lookup before
   a usage error such as a missing `-o`. Consider resolving after those checks. Not a leak.
3. Error messages name the reference verbatim, including whatever follows `kv://` (for example
   `kv://X is not a Key Vault reference`). This is the specified behaviour ("print the reference
   only") and the same as publish; noted only because a user who pastes a secret after a scheme
   prefix by mistake would see it echoed. No action needed for this lane.
4. `command.py:88`: `--client-secret` given with `--auth` other than `spn` is resolved and then
   ignored, while publish refuses it (`--client-secret belongs to --auth spn`). This is pre-existing
   behaviour and outside item (a) ("every other flag unchanged"); worth a line in the 4.11 (b)
   surface decision before the freeze.
5. `plugins/shape-sqlserver/tests/test_client_secret_references.py:120`: the unresolvable-reference
   test passes `-o x.shape`, a path relative to the working directory. It is never written
   because the command exits first, but `tmp_path / "x.shape"` would be safer if the order in
   `_run` changes (see finding 2).
