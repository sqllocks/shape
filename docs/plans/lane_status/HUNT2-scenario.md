# HUNT2-scenario: second-pass bug hunt of scenario packs, `shape demo` and chaos (lane/HUNT2-scenario)

Status: **built; awaiting lead review.** 22 defects filed (#659-#668, #673, #701, #705, #712, #714,
#716, #722, #723, #727, #728, #730, #731), 17 fixed with a failing regression test first, 5 left open
for a reason (below). No gate, tolerance, D-xx or T-xx decision was changed; §11 and §2.3 of the plan
are untouched; the pinned baseline checkout was only read; no workflow file was edited; no PR.

Area: `src/shape/scenario/**`, `src/shape/demo/**`, `src/shape/chaos/**` and their tests (plus the
`pack` command in `src/shape/cli/pack.py`, which is the scenario package's command line). Branch
started from `origin/int/INT-17` (8683435d, still the tip of that branch at the end of the lane).
The first audit (`lane/AUD-scenario`, `docs/plans/lane_status/AUD-scenario.md`, issues #281, #510-#528)
is not on INT-17; none of its findings was filed again and none of its lines was fixed here. Where a
fix here touches the same lines as one of its fixes the lead will see a textual conflict (named in
the table).

## What was run to find them

Reading the integrated code, then trying to break it: empty, huge and wrong-typed inputs, a
hand-edited manifest, session record, connection file and pack, duplicate keys, unicode, a relative
path used from another directory, a composite (multi-domain) run, both kernels and two hash seeds
(`PYTHONHASHSEED`) for determinism, a fuzz of the six chaos categories and the row-level anomalies over
about twenty column types, and an oracle fuzz of the ground-truth log (every logged cell equals the
output, no unlogged change; no problem found).

Checked and found sound (no finding): the run is the same in both kernels and for two hash seeds
(identical dataset id and file bytes with chaos); `shape demo` seeding is byte-identical across both
kernels and repeated runs; versions newer than this release are refused with the release hint for a
pack, a spec, a schema file and a manifest; the ground-truth log of `shape chaos` is exact for the
eight corruptions on mixed column types.

## Findings

Severity: critical / high / medium / low. "Test first" is the commit with the failing output in its
message; "Fix" is the commit.

| # | Sev | Finding | Test first | Fix |
|---|---|---|---|---|
| 659 | medium | `_apply_chaos` runs the referential category inside the per-table loop (6 times for 9 tables); the docs say once | d5204e72 | a4609ff4 |
| 660 | low | the manifest's `volume` chaos count is the rows left (an emptied table counts 0) | d5204e72 | a4609ff4 |
| 673 | medium | a pack with chaos and the `referential_integrity` gate raises `ArrowTypeError` when chaos retyped a key (found while fixing #659: the shipped chaos fixture hit it) | cac30d8f | eec6e89a |
| 661 | medium | value chaos raises on integer columns above 2**53 (`ArrowInvalid`) and on the int64 minimum | e02a72da | 0bce4c7f |
| 662 | medium | a relative local folder is recorded as written, so a cleanup from another directory says "already gone" (exit 0) and leaves the data | f8cb3e47 | f79c0d4b |
| 663 | medium | the demo comparison page prints the real values of columns with a personal-data pattern (email, SSN, ...) | b7f0b1c4 | 7ddf75f7 |
| 664 | medium | a connection profile stores URLs with `user:password@`, SAS `sig=` queries, `Pass=` and `Access Token=` in clear text (only two fields were checked, with a rule that misses them) | f8cb3e47 | f79c0d4b |
| 728 | medium | a session record with a field of a later release cannot be read (`status`, `report`, `cleanup` exit 2); sessions and profiles declare no format or version | 43b1bde7 | dd46f007 (first part) |
| 665 | low | `pack run --json` of an invalid spec has no `success` key (a different shape from an invalid pack) | cc5e425e | 96ab658d |
| 666 | low | `pack list` stops with exit 2 on a folder or broken link named `*.yaml` | cc5e425e | 96ab658d |
| 667 | low | `ManifestBuilder.from_file` checks no field type; `replay` answers MATCH or `AttributeError` for a damaged manifest | 2bf2521a | e99e4143 |
| 701 | low | `demo cleanup --dry-run` says "Would remove" for a local file the real cleanup leaves alone | 0048b440 | a58f0ea5 |
| 705 | low | `pack validate` of a spec ignores `--domain` (a missing domain passes) | c610e17b | 7d859a00 |
| 712 | low | `_write_jsonl` turns a whole table into Python dicts at once (peak +500 MB for a 120 MB file) | 6c6cbc82 | 1ea1a672 |
| 716 | low | `demo run --input-file`: a UTF-16 file and a CSV with a repeated column name give a bare codec message or a traceback, naming no file | d03195c0 | 9b5e2890 |
| 723 | low | a pack or spec with a duplicate key silently keeps the last one (the project file refuses it) | 07a053f2 | 732e00c4 |
| 727 | low | `demo cleanup` of a composite run leaves the empty domain folders and the session folder | 1d2b50bf | b542ec1c |
| 730 | low | a damaged or hand-written `connections.json` entry fails with a bare `AttributeError` or `TypeError` | 95648e99 | c0a18885 |

### Left open

- **#668 (medium): a stream topic's `payload_fields` is ignored; every column is emitted.** Not fixed:
  two existing tests pin the behaviour. `tests/scenario/test_defects.py::test_pk9_stream_events_carry_iso_datetimes`
  and `tests/scenario/test_runner.py::test_stream_writes_one_file_per_matching_topic` use a topic with
  `payload_fields: [order_id]` and read `order_date` from its events. Decision for the lead: change
  those two fixtures to list the columns they read, or keep the key descriptive and drop the validator's
  "no payload_fields defined" warning. The change was written and tested (all 668 tests passed except
  those two) and is reverted; it is this, on top of `lane/HUNT2-scenario`:

  ```diff
  --- a/src/shape/scenario/runner.py
  +++ b/src/shape/scenario/runner.py
  @@ def _write_jsonl(table: pa.Table, path: Path) -> None:
  +def _payload(table: pa.Table, fields: list[str]) -> pa.Table:
  +    """The columns an event carries: the topic's payload_fields that the table has, in the
  +    order listed (every column when none are listed)."""
  +    if not fields:
  +        return table
  +    return table.select([f for f in dict.fromkeys(fields) if f in table.column_names])
  +
  +
  -def _write_jsonl(table: pa.Table, path: Path) -> None:
  +def _write_jsonl(table: pa.Table, path: Path, rows: int | None = None) -> None:
       with path.open("w", encoding="utf-8", newline="\n") as fh:
  +        if table.num_columns == 0:  # no field left: one empty event per row
  +            fh.write("{}\n" * (table.num_rows if rows is None else rows))
  +            return
  @@ def _write_topics(
  -        _write_jsonl(table, target)
  +        _write_jsonl(_payload(table, topic.payload_fields), target, table.num_rows)
  --- a/src/shape/scenario/validator.py
  +++ b/src/shape/scenario/validator.py
  @@ in PackValidator.validate, after self._topics(pack, domain_tables, result)
  +        self._payload_fields(pack, schema, result)
  @@ new method
  +    @staticmethod
  +    def _payload_fields(pack: ScenarioPack, schema: Any, result: PackValidationResult) -> None:
  +        """A topic's payload_fields are columns of the table it stands for."""
  +        tables = list(schema.table_names)
  +        for topic in pack.topics:
  +            table = match_table(topic.name, tables) if topic.name else None
  +            if table is None:
  +                continue
  +            columns = schema.tables[table].columns
  +            for name in topic.payload_fields:
  +                if name not in columns:
  +                    result.errors.append(
  +                        f"Topic '{topic.name}' payload field '{name}' is not a column of "
  +                        f"table '{table}'. Columns: {', '.join(columns)}"
  +                    )
  ```

  The equivalence verifier is not affected (its five inputs list no `payload_fields`).
- **#714 (low): two runs in the same second can overwrite one manifest.** Not fixed: the same lines carry
  #514 (the `_x` suffix), whose fix is on `lane/AUD-scenario`. Make it on top of that fix: create the
  file with `open(path, "x")` and take the next suffix on `FileExistsError`.
- **#722 (low): `shape chaos --input DIR` fails on a folder holding the log of an earlier chaos run.**
  Outside the area (`read_tables` is in `src/shape/cli/incremental.py`): it should skip
  `_chaos_ground_truth*.jsonl`. Filed only.
- **#731 (low): `demo notebook` and `demo report --output` overwrite an existing file.** Owner decision
  (an overwrite switch, or "refuse unless byte-identical"): see the issue.
- **#728, second part: sessions and connection profiles declare no `format` and `version`.** They are
  not kinds of `shape.compat`, and the policy table does not list them. Adding them needs an entry in
  `shape.compat`, rows in `docs/specs/STATE_AND_COMPATIBILITY.md` and a time-capsule file: an owner
  decision, not made here.
- Noticed, not filed as separate issues: `GenerationResult.verify_integrity` passes when the parent key
  column was dropped by schema chaos (named in #673, outside the area); the `spike` volume chaos
  multiplies a table by `10 x intensity` with no cap, so a chaos pack at a large scale preset needs tens
  of times the table in memory (by design of the mutator; a cap would change the documented behaviour);
  `FileChaosMutator` reports one changed byte for a mutation that left the bytes as they were (a file
  with no delimiter, a one-byte file).
- Not run: the emulator-backed and `live` tests (no Docker, no cloud; the `-m "not emulator and not
  live"` selection is what ran). `unshare`-based zero-network job: not run here.

## Fixes that change bytes an equivalence verifier compares

Only #659/#660 (chaos runs). `benchmarks/vs_refengine/pack_1to1/verify.py` was run after the change, and
before it as a baseline:

```
source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/pack_1to1/verify.py
  5 inputs, T-21 columns 64/64, 0 problems, 32.9s     PASS     exit 0
source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/pack_1to1/verify.py --negative-control
  35 problems flagged ...  NEGATIVE CONTROL OK: the harness fails on tampered output     exit 0
```

The verifier checks the chaos input for success, gates, event counts, files and per-table rows and
columns, not the chaos counts, so it holds with the fix. The other fixes do not change the bytes of a
file that a verifier compares (#712 is pinned byte for byte by a test over 130,000 rows).

## Commands and results

Setup: the §1 venv with `.[dev,streaming,advanced]`, every plugin under `plugins/`,
`tests/demo/fabric/requirements.txt`, unixODBC, and the pinned baseline (§1.2, for the verifier only).

### Results (final run, this container)

- `ruff check`, `ruff format --check`, `mypy`, `compileall`, `vulture`, `lint-imports`: clean.
  `check_requirements`, `check_secrets`, `check_user_facing`, `check_shipped_data`,
  `check_plugin_skeletons`, `check_conformance_coverage`: exit 0.
- Full suite `-m "not emulator and not live and not heavy"` (fabric and content folders ignored),
  `SHAPE_KERNEL=rust`: 9218 passed, 18 skipped, 7 failed. `SHAPE_KERNEL=python`: the same, 9218 passed, 7 failed.
- The 7 failures are not from this lane: six `tests/generation/test_composite_p601e.py` cases need the
  pinned baseline package (`sqllocks_refengine`), which is not installed in this container; and
  `tests/streaming/emit/test_faults.py::test_emit_to_two_files` ("table 'member' lacks required
  column(s)") fails the same way on the branch base (8683435d) with none of this lane's commits.
  Not investigated further (outside the lane's area).
- Not run in this session: the coverage-gated and `heavy` steps of `make check`, the Rust steps, the
  emulator and `live` tests. One earlier full run stalled with no CPU use at about 10% and was stopped by
  process id; the re-run passed without a stall.

