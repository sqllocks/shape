"""The blinded clinician review kit (acceptance item 8).

Item 8 is a human step: practising clinicians or claims analysts try to tell generated member
timelines from de-identified real ones.  This module builds everything the reviewers and the owner
need *except the real timelines and the judgements themselves*:

* ``cases/case-NNN.html``: blinded timelines (no ids, no names, no NPIs; specialties only);
* ``scoring_sheet.csv``: one row per case for each reviewer to fill in;
* ``KEY_DO_NOT_SHARE.csv``: which case is which;
* ``README.md``: instructions, the plausibility checklist and the stated pass threshold;
* :func:`score`: reads the filled sheets and the key and reports accuracy and the exact
  binomial p-value.

The real sample is supplied by the owner as a directory of Parquet tables in this domain's schema
(``de-identified``); until it is supplied the kit holds synthetic cases only and the review is
``[VERIFY]``.
"""

from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .report import TimelineIndex, render_member

PASS_ACCURACY = 0.60
PASS_ALPHA = 0.05
MIN_REVIEWERS = 3
MIN_CASES_PER_REVIEWER = 20

README = """# Blinded clinician review kit (acceptance item 8)  [VERIFY: human step]

Status: **not performed**. The kit is built; the judgements are the owner's to collect.

## What the reviewers do
Each reviewer reads every case in `cases/` (a member timeline: coverage, problem list, visits,
stays, procedures, fills and costs) and decides, **without discussing with other reviewers**,
whether the case comes from *real de-identified claims* or was *generated*. They record, on their copy
of `scoring_sheet.csv`:

| column | meaning |
|---|---|
| `case_id` | `case-NNN` (given) |
| `reviewer_id` | your initials or a number |
| `judgement` | `real` or `generated` |
| `confidence` | 1 (guess) to 5 (certain) |
| `implausible_items` | what looked wrong, using the checklist below |
| `notes` | anything else |

## Checklist: what makes a timeline implausible
1. A diagnosis that does not fit the patient's age or sex.
2. A drug with no matching diagnosis, or a diagnosis that is never treated or monitored.
3. A chronic condition that appears once and vanishes, or a lab or screening at an implausible rate.
4. Utilisation out of proportion (too many or too few visits for the problem list, ED use, admissions).
5. A length of stay that does not fit the DRG, or a readmission without a reason.
6. Costs: allowed amounts that do not fit the service, patient cost-sharing that does not fit the plan.
7. Refill timing: gaps, early fills, abandonment that do not look like patients.
8. Claim lifecycle: denials, corrections and reversals at implausible rates.

## Composition
{composition}

## The stated pass threshold
With at least {min_reviewers} reviewers each judging at least {min_cases} cases (half real, half
generated), the generated timelines **pass** when both hold:

1. the pooled accuracy of telling real from generated is at most **{acc:.0%}**; and
2. the one-sided exact binomial test of "accuracy above 50%" does **not** reject at alpha = **{alpha}**.

Score the sheets with `shape healthcare-payer kit-score --key KEY_DO_NOT_SHARE.csv SHEET.csv [...]`.

## Handling
`KEY_DO_NOT_SHARE.csv` stays with the owner. Real timelines must come from claims the owner is entitled to
use and that are de-identified under the applicable rules (HIPAA Safe Harbor or Expert Determination).
Real and generated cases go through the same renderer so layout gives nothing away; if the real sample
uses fewer columns than this schema, say so in the review notes.
"""


@dataclass(slots=True)
class KitResult:
    directory: Path
    cases: int
    synthetic: int
    real: int


def _load_tables(path: Path) -> dict[str, Any]:
    names = ("member", "eligibility", "plan", "provider", "medical_claim", "medical_claim_line",
             "claim_diagnosis", "claim_procedure", "pharmacy_claim", "drug_reference", "member_risk", "rx_adherence")
    return {n: pq.read_table(path / f"{n}.parquet") for n in names}


_LEAKS = re.compile(r"SYN\d|SYS\d|\b9\d{9}\b|@example|synthetic", re.I)


def build_kit(tables: dict[str, Any], directory: str | Path, *, n_synthetic: int = 20,
              real_tables: str | Path | None = None, n_real: int = 20, seed: int = 7) -> KitResult:
    out = Path(directory)
    cases_dir = out / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    from .report import pick_rich_members

    picks: list[tuple[str, str, TimelineIndex]] = []
    ix_syn = TimelineIndex(tables)
    pool = pick_rich_members(tables, max(n_synthetic * 3, 30))
    # the sample must not be the most extreme members only: take a random subset of the rich half
    chosen = [pool[i] for i in rng.permutation(len(pool))[:n_synthetic]]
    picks += [("generated", m, ix_syn) for m in chosen]
    if real_tables is not None:
        real = _load_tables(Path(real_tables))
        ix_real = TimelineIndex(real)
        rpool = pick_rich_members(real, max(n_real * 3, 30))
        picks += [("real", rpool[i], ix_real) for i in rng.permutation(len(rpool))[:n_real]]
    order = rng.permutation(len(picks))
    key_rows = []
    sheet_rows = []
    for n, i in enumerate(order, start=1):
        kind, mid, ix = picks[i]
        case = f"case-{n:03d}"
        html = render_member(ix, mid, blind=True, label=f"Patient {case}")
        if _LEAKS.search(html):
            raise RuntimeError(f"{case}: the blinded page still carries an identifier ({_LEAKS.search(html).group(0)!r})")  # type: ignore[union-attr]
        (cases_dir / f"{case}.html").write_text(html, encoding="utf-8")
        key_rows.append({"case_id": case, "truth": kind})
        sheet_rows.append({"case_id": case, "reviewer_id": "", "judgement": "", "confidence": "", "implausible_items": "", "notes": ""})
    for name, rows in (("KEY_DO_NOT_SHARE.csv", key_rows), ("scoring_sheet.csv", sheet_rows)):
        with (out / name).open("w", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    syn = sum(1 for k, _, _ in picks if k == "generated")
    comp = (f"{syn} generated and {len(picks) - syn} real cases." if real_tables is not None else
            f"{syn} generated cases and **no real cases yet**: add a de-identified real sample "
            f"(`--real-tables DIR`) and rebuild before the review; with generated cases only, no judgement can be scored.")
    (out / "README.md").write_text(README.format(composition=comp, min_reviewers=MIN_REVIEWERS, min_cases=MIN_CASES_PER_REVIEWER,
                                                 acc=PASS_ACCURACY, alpha=PASS_ALPHA), encoding="utf-8")
    return KitResult(out, len(picks), syn, len(picks) - syn)


def binomial_sf(k: int, n: int, p: float = 0.5) -> float:
    """P(X >= k) for X ~ Binomial(n, p), exact."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def score(key_path: str | Path, sheet_paths: list[str | Path]) -> dict[str, Any]:
    key = {r["case_id"]: r["truth"] for r in csv.DictReader(Path(key_path).open(encoding="utf-8"))}
    per: dict[str, list[bool]] = defaultdict(list)
    for sp in sheet_paths:
        for r in csv.DictReader(Path(sp).open(encoding="utf-8")):
            j = (r.get("judgement") or "").strip().lower()
            if j not in ("real", "generated"):
                continue
            per[r["reviewer_id"] or Path(sp).stem].append(j == key[r["case_id"]])
    n = sum(len(v) for v in per.values())
    correct = sum(sum(v) for v in per.values())
    reviewers = len(per)
    enough = reviewers >= MIN_REVIEWERS and all(len(v) >= MIN_CASES_PER_REVIEWER for v in per.values())
    acc = correct / n if n else None
    p = binomial_sf(correct, n) if n else None
    passed = bool(enough and acc is not None and acc <= PASS_ACCURACY and p is not None and p >= PASS_ALPHA)
    return {"reviewers": reviewers, "judgements": n, "accuracy": acc, "p_value_above_chance": p,
            "enough_data": enough, "pass": passed, "per_reviewer_accuracy": {k: sum(v) / len(v) for k, v in per.items()}}
