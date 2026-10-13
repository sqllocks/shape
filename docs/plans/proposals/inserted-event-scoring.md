# Proposal: ground truth for inserted events and text spans, and scorers for both

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`. Amended 2026-10-13: span-level ground truth for text and a span scorer (sections 1b, 2b, phase 2), added to this proposal rather than a separate one because they share the record format, the one-to-one matching and the report.

## 1. The problem

### a. Events

A detection rule, an anomaly model or an alert is tested by planting events and asking which ones it found. The answer needs a ground truth of what was planted (with its key and time) and a scorer that matches detections to it within a time window, and reports precision, recall and the delay to detection.

- Ground truth exists, per feature: chaos change records hold `seed`, `batch`, `table`, `kind`, `row`, `key`, `column`, `before`, `after` (`src/shape/chaos/groundtruth.py:361` to `371`), with no event time; the stream answer key records injected faults (`docs/EMIT.md:157`, `read_answer_key` at `src/shape/streaming/emit/faults.py:151`); the behavior proposals in this folder add group edges and shock attribution.
- Scorers exist but none matches by key and time: the detective `check_answer` matches on `(table, column, mode)` (`src/shape/scenario/detective.py:318` to `322`), game days check that expected checks fired, and pairwise precision and recall exist only for entity resolution (`src/shape/resolve/metrics.py:19`).

### b. Text spans

De-identification, entity extraction and coding from notes are tested the same way, but the unit is a span of text: characters 120 to 131 of note N000123 are a person's name; characters 402 to 431 are the evidence for a diagnosis code. Scoring needs one record per planted or labelled span and a scorer that compares predicted spans with them, exactly or by overlap.

- Nothing records spans. Chaos `pii_fill` replaces whole cells of a free-text column with an SSN, email or phone value (`groundtruth.py:619` to `643`), so its records hold the whole before and after value, not a position inside a text.
- The Presidio integration labels a whole column (the most frequent entity type in a sample of its values, `docs/plugins/integrations.md:140` to `147`), not spans.
- Generated documents are coming (the FHIR DocumentReference proposal in this folder attaches note text), and a text generator that plants names, dates or identifiers into a note knows exactly where it put them. Without a format, that knowledge is lost.

## 2. Proposed design

### a. Event records and the event scorer (phase 1)

- **One ground-truth record shape** for planted things, written by every feature that plants them (chaos, stream faults, behavior groups and shocks, the `financial_patterns` fraud bursts): a header line `{"format": "shape-ground-truth", "version": 1, "offset_unit": "codepoint"}`, then JSON lines. An event record has `id`, `record: "event"`, `source` (the feature), `kind`, `table`, `key` (one value or a list), `time` (or `start` and `end`), and free `details`. Existing logs keep their format; a converter maps each to this shape.
- **`shape score events TRUTH DETECTIONS`**: detections are a table or JSON lines with `key`, `time` and optional `kind` and `score`. A detection matches a truth record when the keys match (exactly, or any member of a list), the kinds match when both are given, and the time lies in `[start - before, end + after]` (`--window-before`, `--window-after`). Each truth record matches at most one detection (the earliest), and each detection at most one record.
- **Report** (`shape-score-report`, versioned): true and false positives, false negatives, precision, recall, F1, per kind; the delay from `time` to the matching detection (median, 90th percentile); with `score`, precision and recall at each threshold.

### b. Span records, the validator and the span scorer (phase 2)

A span record in the same file:

```json
{"id": "s-000123-1", "record": "span", "source": "notes", "document": {"table": "clinical_note", "key": "N000123", "column": "text"},
 "start": 120, "end": 131, "quote": "Maria Lopez", "type": "PERSON", "attributes": {"role": "patient"}}
{"id": "s-000123-7", "record": "span", "source": "notes", "document": {"table": "clinical_note", "key": "N000123", "column": "text"},
 "start": 402, "end": 431, "quote": "poorly controlled blood sugar", "type": "EVIDENCE", "attributes": {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "E11.65"}}
```

- `document` names a text cell (table, key, column) or carries a free `id` for text outside a table. `start` is inclusive and `end` exclusive. `offset_unit` in the header says how offsets count: `codepoint` (Python string indices, the default), `utf16` (JavaScript and Java) or `utf8` bytes, so a consumer in another language reads them without guessing. `quote` is the exact text between the offsets. `type` is the label; `attributes` are free (a code, a role, a normalized value).
- Labelled spans (a person annotated existing text) use the same record with `"source": "annotation"`.
- **Validator** `shape score validate TRUTH --documents DATA`: for every span, the document exists, `0 <= start < end <= len(text)`, the text between the offsets equals `quote` in the declared unit, and ids are unique. Overlapping spans of the same type are reported as a warning (`--no-overlap` makes them an error). Exit 0 valid, 1 a problem with the list of bad records, 2 usage.
- **`shape score spans TRUTH PREDICTIONS`**: predictions are JSON lines with `document`, `start`, `end`, `type` and optional `score` and `attributes`, in the same `offset_unit`.
  - **Strict**: same document, same `start` and `end`, same `type`.
  - **Lenient**: same document and type, and the overlap is at least `--min-overlap` (default: one character) of the union, or with `--overlap-of truth` of the truth span.
  - **Type-agnostic** (`--ignore-type`): either mode without the type, for "was anything found here".
  - **Attributes** (`--match-attributes code`): a matched span counts as correct only if the listed attributes are equal too, which scores coding evidence (right place and right code).
  - Matching is one-to-one: candidate pairs are taken in order of overlap (largest first, ties by position), so a long prediction cannot claim two truth spans.
  - The report gives true positives, false positives and false negatives, precision, recall and F1 per type, micro and macro averages, and samples of each error (`quote` against the predicted text). Strict and lenient results are both written in one report.

## 3. Compatibility

New formats only. Chaos logs (`record`/`log_version`, `groundtruth.py:229`) and the stream answer key stay as they are and are read by the converter. The ground-truth format is one format with two record kinds, so a file can hold events and spans; a version 1 reader that meets a record kind it does not know refuses the file naming the kind. Nothing generated changes, so no generator version or seeded output changes.

## 4. Wanted (ready to paste into the issue)

Phase 1, events:
1. The `shape-ground-truth` format (header with `format`, `version`, `offset_unit`; event records) and converters from the chaos log and the stream answer key.
2. `shape score events` with key, kind and time-window matching, one-to-one assignment, and the versioned report of section 2a; exit 0, or 2 for bad input.
3. A Python function `shape.quality.score.events(truth, detections, ...)` returning the same report.

Phase 2, spans:
4. Span records (`document`, `start`, `end`, `quote`, `type`, `attributes`) in the same format, with `offset_unit` `codepoint`, `utf16` or `utf8`.
5. `shape score validate` checking offsets against quoted text in the declared unit, bounds, unique ids and overlaps.
6. `shape score spans` with strict, lenient (`--min-overlap`, `--overlap-of`), type-agnostic and attribute matching, one-to-one assignment, per-type and averaged precision, recall and F1, and error samples; `shape.quality.score.spans(...)` in Python.
7. Chaos `pii_fill` also writes span records (the whole cell as one span), so a de-identification test can be scored without a new generator.
8. Documentation with two examples: plant fraud bursts and score a rule; plant names in notes and score a de-identification tool's output in both modes.

Acceptance beyond section 13's common items: on hand-built cases, detections just inside and just outside the window are counted correctly; a detection or prediction never matches two records; a span whose quote differs from the text by one character, or whose offsets are in the wrong unit for non-ASCII text, is reported by the validator; strict and lenient scores equal a reference computation on a fixed set including overlapping, nested and adjacent spans; counts and rates equal a reference computation.

## 5. Open questions for the lead
1. Whether the scorers belong in core or in the scenario tools next to the detective packs.
2. Whether behavior ground truth (group edges, shock attribution) should be written in this format from the start.
3. Whether a common annotation format (for example a BRAT standoff or a JSON layout used by annotation tools) should be readable and writable by a converter, so existing labelled corpora can be scored.
