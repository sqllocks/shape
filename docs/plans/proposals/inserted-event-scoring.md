# Proposal: ground truth for inserted events and a scorer by key and time window

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`. Lower priority: a short proposal.

## 1. The problem

A detection rule, an anomaly model or an alert is tested by planting events and asking which ones it found. The answer needs a ground truth of what was planted (with its key and time) and a scorer that matches detections to it within a time window, and reports precision, recall and the delay to detection.

- Ground truth exists, per feature: chaos change records hold `seed`, `batch`, `table`, `kind`, `row`, `key`, `column`, `before`, `after` (`src/shape/chaos/groundtruth.py:361` to `371`), with no event time; the stream answer key records injected faults (`docs/EMIT.md:157`, `read_answer_key` at `src/shape/streaming/emit/faults.py:151`); the behavior proposals in this folder add group edges and shock attribution.
- Scorers exist but none matches by key and time: the detective `check_answer` matches on `(table, column, mode)` (`src/shape/scenario/detective.py:318` to `322`), game days check that expected checks fired, and pairwise precision and recall exist only for entity resolution (`src/shape/resolve/metrics.py:19`).

## 2. Proposed design

- **One ground-truth record shape** for planted events, written by every feature that plants them (chaos, stream faults, behavior groups and shocks, the `financial_patterns` fraud bursts): `{"format": "shape-ground-truth", "version": 1}` header, then JSON lines with `id`, `source` (the feature), `kind`, `table`, `key` (one value or a list), `time` (or `start` and `end`), and free `details`. Existing logs keep their format; a converter maps each to this shape.
- **`shape score TRUTH DETECTIONS`**: detections are a table or JSON lines with `key`, `time` and optional `kind` and `score`. A detection matches a truth record when the keys match (exactly, or any member of a list), the kinds match when both are given, and the time lies in `[start - before, end + after]` (`--window-before`, `--window-after`). Each truth record matches at most one detection (the earliest), and each detection at most one record.
- **Report** (`shape-score-report`, versioned): true and false positives, false negatives, precision, recall, F1, per kind; the delay from `time` to the matching detection (median, 90th percentile); with `score`, precision and recall at each threshold.

## 3. Compatibility

New formats only. Chaos logs (`record`/`log_version`, `groundtruth.py:229`) and the stream answer key stay as they are and are read by the converter. Nothing generated changes.

## 4. Wanted (ready to paste into the issue)

1. The `shape-ground-truth` record shape and converters from the chaos log and the stream answer key.
2. `shape score` with key, kind and time-window matching, one-to-one assignment, and the versioned report of section 2; exit 0, or 2 for bad input.
3. A Python function `shape.quality.score(truth, detections, ...)` returning the same report.
4. Documentation with an example: plant fraud bursts, run a simple rule, score it.

Acceptance beyond section 13's common items: on hand-built cases, detections just inside and just outside the window are counted correctly; a detection never matches two records; counts and rates equal a reference computation.

## 5. Open questions for the lead
1. Whether the scorer belongs in core or in the scenario tools next to the detective packs.
2. Whether behavior ground truth (group edges, shock attribution) should be written in this shape from the start.
