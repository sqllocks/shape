# BUGS-kernel-2 — `string_case` native vs twin outside ASCII (issue #547)

Status: built; all checks below pass in this session.

## Change (commit ccb40e0)

The native kernel is the contract (decision on #547): `title` splits words with Rust's
`char::is_alphanumeric`, `upper` and `lower` use the Rust standard library's case tables, and
`lower` applies `Final_Sigma`. Native output is unchanged (no Rust change, no new crate).

- `scripts/gen_unicode_case_table.py` probes the native kernel on every code point and writes
  `src/shape/kernel/reference/unicode_case.json` (package data, 80 KB). `--check` fails when the
  file is stale.
- `src/shape/kernel/reference/gen.py`: the twin reads that table outside ASCII instead of the
  running Python's Unicode data; ASCII paths are unchanged.
- `tests/kernel/test_unicode_case.py`: the issue's examples, Final_Sigma contexts, random text,
  and sweeps of every code point in all three modes (alone, inside words, as Final_Sigma
  context; marked `heavy`).
- `docs/GENERATION_KERNEL.md`: new section "Case outside ASCII".

## Checks run in this session

Environment: Python 3.11.15, `pip install -e '.[dev,streaming,advanced]'` plus the
`shape-domains`, `shape-kafka`, `shape-eventhubs`, `shape-sqlserver` and `shape-fabric`
plugins (the `tests/demo_cmd` semantic-model tests need `shape-fabric`).

- `make check`: exit 0. Ruff, format, mypy, vulture, lint-imports and every `scripts/check_*.py`
  (including `check_user_facing.py`) are clean. Main pytest step: 6792 passed, 2 skipped (the
  `shape-databases` plugin is not installed), coverage 92.06% (gate 86%). Heavy step: 49 passed.
  `SHAPE_KERNEL=python pytest tests/kernel`: 276 passed. cargo fmt, clippy and test: clean.
- `SHAPE_KERNEL=python pytest -m "not emulator and not live and not heavy"` (CI's selection,
  same ignores): 6792 passed, 2 skipped.
- `SHAPE_KERNEL=python pytest -m heavy tests/kernel` (the every-code-point sweeps on the
  twin path): 46 passed.
- `python scripts/gen_unicode_case_table.py --check`: up to date.
- `maturin build --release` then `python scripts/check_shipped_data.py --wheel <wheel>`: OK; the
  wheel contains `shape/kernel/reference/unicode_case.json`.

## Notes

- I stopped an earlier `SHAPE_KERNEL=python` run that included heavy tests outside
  `tests/kernel`. `tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows`
  profiles a 48M-row CSV through the pure-Python kernel and was still running after more than 12
  minutes. CI runs heavy tests on the native kernel only, so this is not a regression and the
  lane does not touch it.
- No `.github/workflows` change.
