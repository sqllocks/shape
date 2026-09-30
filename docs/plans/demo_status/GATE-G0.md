# Gate G0

Commit checked: `b965672` (branch `build/main-plan`). Recorded by the builder session; every result
below was seen in that session, on the machine described under "Environment".

## 1. CI is green

CI run 36727199284 (workflow CI) on `b965672`: all 12 jobs concluded `success` (test on
ubuntu 3.11/3.12/3.13/3.14, macos 3.11/3.14, windows 3.11/3.14; audit; build; fabric-demo;
bench-quick). Security run 36727199293 on the same commit: success.

Earlier runs on this branch failed and were fixed before this record:
- run 73: Windows legs failed on two of my new harness tests (a cp1252 read, a path comparison); fixed in `64e42ec`.
- run 74: `bench-quick` passed all verifiers but the artifact upload rejected a `..` path; fixed in `b965672`.

## 2. `run.py --quick` works from scratch

- In CI (`bench-quick`, a fresh runner): `setup_spindle.sh` (pinned Spindle 422e78df), the Shape venv,
  `check_coverage.py`, then `run.py --quick`: verdict pass on every workload.
- Locally, after the section 1 setup was done from nothing in this session:
  `source scripts/env.sh && python benchmarks/vs_spindle/run.py --quick` exited 0 and wrote a
  schema-valid `benchmarks/vs_spindle/results.json` (also valid under `jsonschema` Draft 2020-12).
  Verifier status and numbers for `spindle` and `reference_port`; `shape` is `null`.

| workload | verifier | Spindle | reference_port | ratio |
|---|---|---|---|---|
| profile D1 CSV | pass | 1.55 s | 0.33 s | 4.7x |
| profile D1 Parquet | pass | 1.33 s | 0.34 s | 3.9x |
| profile D2 CSV | pass | 31.73 s | 2.86 s | 11.1x |
| profile D2 Parquet | pass | 26.30 s | 2.27 s | 11.6x |
| retail small | pass | 0.25 s | 0.13 s | 1.9x |
| retail medium | pass | 5.79 s | 0.98 s | 5.9x |

These are numbers for the reference port (the pre-Rust 1:1 port), not for the product; they are
the section 6.2(4) reference until nightly runs exist. Ratios on a shared 4-core builder vary by
about 10%.

Other commands run, and results:
- `profile_1to1/verify.py --impl reference_port` (all 30 datasets, before and after the ruff cleanup): 30/30 PASS, exit 0; per-field matrix identical before and after.
- `domain_1to1/verify.py --domain retail --scale small|medium --impl reference_port`: 60/60 columns equivalent, VERDICT PASS, exit 0 (medium also re-verified on the timed output).
- Negative checks: a corrupted `order_line.unit_price` made `verify.py` exit 1; a missing `_SUCCESS` with `--no-generate` made it exit 2.
- `check_coverage.py`: OK, all 277 Spindle files mapped.
- `grep -rnE '/tmp/|/home/' benchmarks/ --exclude-dir=baselines --exclude-dir=__pycache__`: no output.
- `make check`: exit 0 (ruff, format, mypy, compileall, requirements, secrets, 699 tests, coverage 86.55% against a floor of 86%).
- `pytest tests/demo` (in a venv with `tests/demo/fabric/requirements.txt`, Java 17, unixODBC, `SPINDLE_ROOT` set): 188 passed.

Not run here: `run.py --full` (only `--dry-run`); it runs in the nightly workflow.

## 3. SEC1, SEC2, SEC4, SEC5 have regression tests

- SEC1: `tests/privacy/test_advanced.py` (fixed-seed Laplace removed).
- SEC2: `tests/privacy/test_release_policy_new.py` (value keys removed; `source_exceeds_target`; `cohort_below_minimum`).
- SEC4: `tests/registry/test_local_registry.py::test_checkout_other_names_object_rejected`.
- SEC5: `tests/registry/test_local_registry.py::test_bad_names_rejected`, `::test_symlink_escape_rejected`.
`pytest tests/registry tests/privacy`: 25 passed.

## 4. Baselines present and unchanged

`benchmarks/baselines/2026-09-29/` has 12 files. `git log --diff-filter=MD -- benchmarks/baselines`
is empty: they were added and never modified or deleted.

## Environment

Python 3.11.15; Spindle venv per section 1.2 (pandas 3.0.6, numpy 2.4.6, pyarrow 25.0.1); Shape venv
with pyarrow 25.0.1; a separate venv with pyarrow 19.0.1 for the Fabric demo tests. 4 cores.

## Notes for the lead

- The `bench-quick` job runs on `ubuntu-latest`; on a private repo that is 2 vCPUs, so its ratios are informational until the repo is public (O-05).
- A stray `/ratchet.txt` (empty-variable redirect target) exists outside the repo; my `rm` was refused by the permission guard and I left it. Harmless.
