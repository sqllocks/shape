# Available features: claim-to-proof ledger

Status: experimental.

This ledger records the proof required to label a documentation page **available**.
`python scripts/check_available.py` checks each promoted page against P-BAR, and
`make check` runs it. No page has a promotion section here yet. Existing available
pages remain exempt through the committed, shrink-only legacy list.

The check proves that named proof exists, not that it passes. The test suite and
CI prove that it passes. The check cannot judge whether the marked claims include
all the key claims; a reviewer must check that coverage.

Each page section uses a level-two heading containing its repository-relative
docs path, followed by one plain `since: <version>` line and a five-column table.
The columns are claim ID, short claim, proof, evidence and status. For example,
the heading is `## docs/CANARIES.md`, and a row has the form
`| AV-CANARIES-01 | claim (short) | proof | evidence | status |`.
The `since:` value names the release that first promotes the page.

Mark each key claim on its page with `<!-- claim: AV-CANARIES-01 -->`.
Every anchor must have one row, and every row must have one anchor. An available
page needs `done` rows. An `open <work package>` row records unfinished work and
prevents promotion.

The proof cell names pytest node IDs, such as
`tests/scenario/test_canary.py::test_make_writes_the_marked_batch_and_the_canary_document`,
or CI jobs, such as `ci:ci.yml/test`, in backticks. A parameter family or test
class may be named without its parameters or methods.

A page declares its commands under its `Status:` line, for example
`Covers: shape profile, shape diff`. Every declared or mentioned command must be
stable. An undeclared mention fails the check. Surround experimental parts with
`<!-- experimental:start -->` and `<!-- experimental:end -->`; the site renders
them in a warning box, and the check skips their commands and claim anchors.
Each delimiter occupies its own line.
Open owner notes anywhere on the page still prevent promotion.

A claim that needs an outside service carries a `service:<name>` tag in its
claim cell. Its evidence cell names committed files under `docs/evidence/` or
`docs/plans/evidence/`, in backticks. Use `none` when no service is touched.
A self-hosted service counts as real; a mock or cloud emulator does not. A test
marked `emulator` alone is not evidence.

A JSON transcript records `run_id`, `commit`, `command`, `exit_code: 0`,
`status: passed`, `shape` (or `shape_version`) and an ISO `date`. An Owner receipt
records its command in the file and includes a machine line of the form
`<!-- receipt: shape=<version> service=<name> exit=0 date=<YYYY-MM-DD> -->`.
Evidence is fresh if its Shape version equals the version in `pyproject.toml`.
Otherwise it also needs a `commit` and `paths` naming the source files, and Git
must confirm those paths are unchanged between that commit and HEAD. JSON uses
a list of paths; a machine receipt uses comma-separated paths.

Every page needs existing proof, stable commands and no open owner notes. A
page with `since:` equal to the current package version also needs a line naming
the page under `### Promoted` in that version's CHANGELOG section. If the version
section does not exist, the check uses `## Unreleased`.

`tests/docs/available_legacy.txt` lists the existing exempt pages, sorted, one
path per line. Every entry must still exist and say `Status: available`. Remove
an entry when its page meets the full rules; the list may only shrink. A page
recorded in this ledger cannot later be demoted to experimental.

The growth check compares the list with its value at the merge base of HEAD and
`origin/main`. Introducing the file at that base needs no growth comparison.
Without a usable Git baseline, the local check prints one notice to stderr.
CI runs `--require-base`, which makes an unavailable baseline an error.

See [the definition of done](V1_DONE.md) for the future 1.0 promises and
[CLI stability](CLI_STABILITY.md) for command policy.
