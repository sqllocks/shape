# HC-domain: issue #45, lane "healthcare payer domain pack": status

Branch `lane/HC-domain`. Plugin: `plugins/shape-domains`, package
`shape_domains.healthcare_payer` (the distribution `sqllocks-shape-domains`; no new distribution).
Page: `docs/domains/healthcare_payer.md` (calibration table, measured evidence, limits). The
existing `healthcare` domain is untouched. No D-xx/T-xx decision, gate or tolerance is changed; §11
and §2.3 are unedited; `$SPINDLE_ROOT` is untouched; no test is skipped (two tests use
`importorskip` for a plugin that is absent in some installs: see "Checks"); the issue was not
commented on, labelled or closed; no PR. Commit prefix `HC-domain:`.

## What I depend on (sibling lanes)

* **`lane/HC-codes`** (merged): `AssetCodes` / `FdaNdcDirectory` in `codes_adapter.py` read its
  assets (`icd10cm`, `icd10pcs`, `hcpcs2`, `ndc`, `pos`, `hcc`, `mce_edits`, `ccsr`). The domain
  runs without them (own seed sets) and uses them for validation and, with `--ndc fda`, for real
  NDCs. Reading `hcc`, `mce_edits` and `ccsr` goes through `shape_healthcare_codes.store.read_table`
  (they are plain tables, not code sets).
* **`lane/HC-behavior`** (merged): `behavior_engine.py` runs `shape_behavior.Simulator` on the
  documents in `behavior_modules.py`. Optional: `--engine behavior` needs the plugin; the native
  engine does not.
* **`lane/HC-standards`** (not merged, not mine): the column names of the tables follow its input
  contract. `tests/healthcare_payer/test_standards_contract.py` checks them against a **snapshot**
  of that contract (`standards_contract_snapshot.json`). With the real plugin the check is, and
  I ran it (see "Checks"):
  ```
  PYTHONPATH=<path to>/plugins/shape-healthcare-standards/src python - <<'E'
  from shape_healthcare_standards import contract as c
  from shape_domains.healthcare_payer.generate import generate
  d = generate(600, seed=5)
  for n, t in d.tables.items():
      try:
          out = c.coerce(n, t)
      except c.ContractError:        # a table the contract does not list
          continue
      assert not c.check_keys(n, out)
  E
  ```

## Acceptance (issue #45), with evidence

Evidence is `shape healthcare-payer quality --members 3000 --seed 21 --ndc fda` (native) and the
same with `--engine behavior`; the full tables are in section 8 of the domain page. Numbers below
are from that run.

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Age and sex edits | pass | 0 violations in 136,716 diagnoses; also 0 against the CMS MCE edits of the codes lane |
| 2 | Diagnosis, procedure and drug agree | pass | 0 fills without a supporting diagnosis, 0 order/indication mismatches; diabetic members: 96% HbA1c, 80% eye exam, 83% an antidiabetic fill |
| 3 | Comorbidity clustering, persistence | pass | P(htn, lipid, obesity, CKD given diabetes) = 0.69, 0.60, 0.58, 0.21 on the problem list, inside the bands; next-year recurrence 0.96 to 1.0 |
| 4 | Utilisation shape | pass, thin margin | top 5% of members carry 0.42 of cost (band 0.38 to 0.62); ED and admits per 1,000 inside the band for each line; winter to summer respiratory 2.6 |
| 5 | Every code valid on the date of service | pass | against the codes lane's full release history: 0 invalid ICD-10-CM, 0 invalid ICD-10-PCS, 0 missing HCPCS, **0 of 49,868 fills with an NDC not marketed on the fill date** (FDA directory) |
| 6 | Financial coherence | pass | 0 claim, line or fill rows where allowed ≠ paid + member responsibility; 0 accumulator rows above the maxima |
| 7 | LOS and DRG; readmissions rare | pass | 574 stays, 0 outside the DRG range, 30-day readmission 0.056 (band 0 to 0.15) |
| 8 | Blinded clinician review | **`[VERIFY]` (owner)** | kit produced (`shape healthcare-payer kit`: 20 cases, scoring sheet, key, instructions) and scoring tested; **the review has not been done** and the kit holds no real cases, so it cannot show indistinguishability |
| 9 | HTML member timeline | pass | `shape healthcare-payer timeline`: self-contained pages, tested for structure |

Other scope points: subscriber/dependent structure, spans with gaps and re-enrolment, PCP
attribution, COB flag and coherent addresses (`test_structure.py`); claim lifecycle with CARC/RARC
denials, adjustments, reversals, resubmissions, prior authorization, duplicates
(`test_structure.py`, `test_cost_sharing.py`); pharmacy fills, rejects, reversals, adherence
(`test_pharmacy.py`); provider directory; HCC/RAF (`member_risk`); clinical modules for diabetes,
hypertension, hyperlipidemia, CKD, asthma/COPD, pregnancy (with newborns), a cancer pathway,
behavioral health and respiratory seasonality, written for this pack (`test_clinical_modules.py`).
Synthetic identifiers (`test_identifiers.py`). Calibration table with a public source per rate and
`calibrate.from_tables` (`test_determinism_and_calibration.py`). Standards-ready tables (15 of them,
the contract's names).

## Findings from the real assets (fixed)

Cross-checking my seed against the codes lane's real releases found four date edits to fix, now in
`icd10cm.py` and tested: `E78.01` became a non-billable header on 2025-10-01 (`E78.010/.011/.019`),
`U07.1` is valid from 2020-10-01 in the release history, `J12.82` from 2021-01-01, `Z13.31` from
2018-10-01. `AssetCodes.cross_check_seeds()` now returns `[]`. The FDA directory matching also
needed per-mL strengths for pens, brand first word, and form tokens (`semaglutide`, `tiotropium`,
`etanercept` had no packages); all 81 drugs now match (880 packages).

## Checks run in this session

| Check | Result |
|---|---|
| `ruff check` and `ruff format --check` on `src tests plugins benchmarks/vs_spindle` | clean (978 files) |
| `mypy --strict src` | no issues (345 files) |
| `mypy --strict` on the package, with the repo venv's interpreter (`--python-executable ~/.venvs/shape/bin/python`) | no issues (32 files) |
| `vulture src/shape scripts/vulture_whitelist.py --min-confidence 80`, and on the plugin source | clean |
| `lint-imports` | 1 contract kept, 0 broken |
| `python scripts/check_user_facing.py` | clean |
| `python scripts/check_user_facing.py --wheel <the built sqllocks_shape_domains wheel>` | clean |
| `python scripts/check_plugin_skeletons.py` | OK (8 distributions) |
| `bandit -q -r src -ll`, `bandit -q -r plugins/shape-domains/src -ll` | no findings (exit 0) |
| START (median of 10 `shape --version`) | 58 ms (budget 300 ms) |
| `python -m shape.plugins.kit sqllocks-shape-domains` | OK, 2 plugins conform (the `healthcare-payer` command and the older `retail` domain) |
| `pytest plugins/shape-domains/tests/healthcare_payer` | 125 passed |
| standards contract, real `contract.coerce` and `check_keys` on 600 members | 9 of the 15 tables are contract tables and all coerce with no key errors; the other six (`plan`, `prior_authorization`, `rx_order`, `rx_adherence`, `member_risk`, `member_accumulator`) are not in the contract (the domain's own extras) |
| main suites with `SHAPE_KERNEL=rust` and `SHAPE_KERNEL=python` | 5229 passed each (see "Suites") |

The one measured-and-reported gap: HbA1c tests per diabetic member-year are 1.9 (native) against
the table's 2.6 (ADA 2 to 4); the check allows 0.6x to 1.4x of the target. I did not change that
allowance.

## Suites

`SHAPE_KERNEL=rust`: 5229 passed, 46 deselected. `SHAPE_KERNEL=python`: 5229 passed, 46 deselected
(both `-m "not emulator and not live and not heavy" --ignore=tests/demo/fabric`, run after the merges of
`lane/HC-behavior` and `lane/HC-codes`). The plugin's own tests (125) run separately, as above.

## What is not done, and what I could not verify

* **Item 8 is a human step**: `[VERIFY]` (owner). Nothing in this lane claims the data passes it.
* **Calibration figures marked cited `[VERIFY]` were recalled from the named publications, not
  re-fetched.** A reviewer must confirm them; rows marked *assumption* are modelling choices.
* HCC weights are illustrative, DRG weights and geometric mean stays are approximate (a seed of 43
  DRGs, not the CMS grouper): all `[VERIFY]`.
* The behavior engine covers diabetes, hypertension and lipid only, without diabetes complications,
  hypoglycaemia/DKA admissions or supplies.
* County is null (the ZIP reference has no county column).

## For the lead (escalations; none is mine to decide)

1. **Not under `shape.domains`.** The group's contract is a column-wise schema with fixed row
   counts; this domain is an event simulation whose counts emerge. The pack is a
   `shape.commands` entry (`healthcare-payer`) and a Python API. A table-producing hook on the
   `Domain` protocol would be a core change.
2. **Bring-your-own code sets:** CPT, revenue codes, type of bill and NUCC taxonomy are not shipped
   (the codes lane found NUCC to need a licence for commercial use: owner decision pending);
   CARC/RARC official text is copyrighted, so only codes with my own labels are shipped.
3. **CI wiring** for the new plugins (`sqllocks-shape-healthcare-codes`, `-behavior`) and for this
   lane's `plugins/shape-domains/tests/healthcare_payer` is not in a workflow; the plugin's tests
   take about 3 minutes and one of them regenerates the 3,000-member population.
4. **The standards-contract snapshot** in the tests is a copy; it must be refreshed when that lane
   changes the contract.
5. **Thin margin** on the cost tail (top 5% carry 0.42 of cost; band lower edge 0.38).
6. **Interim NDC directory** is the offline default; `--ndc fda` is the real one.
