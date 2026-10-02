# Healthcare payer domain (`healthcare_payer`)

Members, eligibility, medical claims, pharmacy claims, a provider directory and risk scores for a
mixed commercial, Medicare Advantage and Medicaid population, generated from simulated clinical
events so that diagnoses, procedures, labs and drugs agree by construction (issue #45). The
`healthcare` domain is a different, older domain and is untouched.

```bash
shape healthcare-payer generate --members 5000 --seed 7 -o payer/            # 15 Parquet tables
shape healthcare-payer quality  --members 3000 --seed 21                     # acceptance items 1-7
shape healthcare-payer timeline --members 2000 -o timelines/ --count 10      # HTML member timelines
shape healthcare-payer kit      --members 2000 -o review-kit/                # blinded review kit
shape healthcare-payer calibration                                           # every rate and its source
```

```python
from shape_domains.healthcare_payer import generate

data = generate(5000, seed=7)           # a HealthcarePayerData
data.tables["medical_claim"]            # Arrow tables, see section 4
data.write("payer/", "parquet")
```

## 1. Where it lives and how it is exposed

* **Plugin:** `plugins/shape-domains` (distribution `sqllocks-shape-domains`), package
  `shape_domains.healthcare_payer`. It is the `shape.domains` distribution, so the pack sits with
  the other domains; a new distribution would not be one of the T-09 first-party plugins that
  `scripts/check_plugin_skeletons.py` lists.
* **Not registered under `shape.domains`.** That group's contract is a column-wise generation
  schema with fixed row counts per table (`DomainDefinition`). This domain is an event simulation:
  claim and fill counts *emerge* from it and a column cannot be generated without the member's
  history. The pack is exposed through the `shape.commands` entry `healthcare-payer` and the Python
  API above. Extending the `Domain` protocol with a table-producing hook is a core change and is
  left to the lead (lane status, "For the lead").
* **Determinism:** the same `(members, seed, window, calibration)` gives byte-identical tables. Every
  member has its own random stream keyed by `(seed, member index)`.

## 2. How the data is made

```
population + plans + eligibility ─┐
provider directory                ├─► virtual-clock simulation (one member at a time)
calibration table (public rates)  ┘        │ clinical modules schedule dated events
                                           ▼
                       encounters (visits, ED, stays, labs, procedures)  +  drug courses
                                           │
              claim drafts ─► lifecycle ─► pricing and cost sharing ─► medical_claim, lines, diagnoses
              fills ─► adherence, rejects, reversals, costs      ─► pharmacy_claim, rx_order, rx_adherence
              paid-claim diagnoses ─► HCC / RAF                  ─► member_risk
```

No claim is invented after the fact: a claim exists because an encounter happened, and an
encounter happens because a module scheduled it. The runtime (`engine.py`) is the domain-local
equivalent of the behavior engine's contract: a virtual clock, dated state-machine events and
deterministic per-entity streams. `behavior_engine.py` (section 9) runs modules written as behavior-engine documents through the
same claims pipeline.

**Clinical modules** (all written for this pack; no Synthea code or modules):

| Module | What it does |
|---|---|
| `cohort` | Who has which chronic condition at the start and who develops one, with comorbidity clustering (odds ratios on top of age prevalence; the base odds are solved so each marginal matches its published prevalence) |
| `diabetes` | Type 1 and 2; reviews, HbA1c, lipid, kidney labs at elapsed-time rates, annual eye, foot and urine albumin exams, supplies, complications, hypoglycaemia and DKA admissions; metformin, newer agents, insulin and statin starts |
| `htn`, `lipid` | Review cadence, labs, first-line to third-line regimens matched to the comorbidities, statins and add-ons |
| `ckd` | Stages 1-5 and ESRD with progression, nephrology visits, dialysis (monthly claims), AKI admissions |
| `resp_chronic` | Asthma and COPD: controllers, rescue inhalers, seasonal exacerbations that go to the office, urgent care, ED or the hospital |
| `behavioral` | Depression, anxiety, ADHD, opioid and alcohol use disorders, bipolar disorder, schizophrenia: visits, medicines, crisis admissions |
| `cardiac`, `simple` | Coronary disease, heart failure, atrial fibrillation; thyroid, reflux, osteoporosis, prostate, autoimmune disease on biologics |
| `pregnancy` | Conception by age, prenatal schedule, gestational diabetes and hypertension, delivery (vaginal or cesarean, DRG), postpartum visit and the **newborn**, who becomes a dependent member with their own stay and later care |
| `cancer` | Breast, prostate, colon and lung: work-up, biopsy, diagnosis, staging, surgery (ICD-10-PCS with laterality), chemotherapy cycles, radiation, endocrine therapy, survivorship, progression and death |
| `acute` | Winter-weighted respiratory illness, influenza, COVID-19 waves, injuries, back pain, UTI, appendicitis, gallbladder disease, joint replacement, cataract, screening and prevention (mammogram, colonoscopy, cervical, PSA, flu vaccine, wellness) |
| `events` | Readmissions, terminal stays, background medical admissions concentrated in the multi-morbid |

Stays derive their **DRG from the diagnoses** (family from the principal diagnosis, tier from the
CC and MCC secondary diagnoses) and their **length of stay around that DRG's geometric mean**, so
LOS and DRG agree by construction. The DRG logic is a transparent seed of the CMS grouper, not the
grouper (limits, section 12).

## 3. Population, plans and eligibility

* **Mix:** commercial 55%, Medicare Advantage 20%, Medicaid 25% by default (`lob_mix`); age and sex
  per plan from the calibration table. Commercial households have a subscriber (relationship 18),
  often a spouse (01) and children (19) to age 26; Medicaid cases have a parent and children;
  MA members are individuals, 90% aged 65+ and the rest disabled.
* **Plans:** five commercial designs (PPO, EPO, HMO, HDHP) and two MA, one Medicaid. Deductibles and
  out-of-pocket maxima sit below the regulatory limit of every year of the window; the HDHP
  deductible is at least the IRS minimum (tested).
* **Eligibility:** spans with terminations, **gaps and re-enrolment** (Medicaid churns most), new
  hires, dependents aging off at 26, deaths. The clinical history runs through gaps; only covered
  services become claims, so a re-enrolling member's chronic conditions reappear in their claims.
* **PCP attribution** (assigned for HMO and MA-HMO, attributed otherwise), **COB flag**, dual
  eligibility, **addresses** from the bundled ZIP reference (state, city, ZIP, coordinates; county
  is null because that reference has no county column).

## 4. Tables

Column names follow the standards lane's input contract
(`shape_healthcare_standards.contract`), so the X12, FHIR and OMOP writers read these tables as they
are (`tests/healthcare_payer/test_standards_contract.py` checks it). Money is `float64` to cents with
`precision` 12 and `scale` 2 in the field metadata. ICD-10 codes are on a row twice: `icd10_code`
without the decimal point (claim-file form) and `diagnosis_code` with it.

| Table | One row per | Key columns |
|---|---|---|
| `member` | member | demographics, subscriber id and suffix, relationship code, address, line of business, PCP, COB flag |
| `eligibility` | coverage span | plan, group, coverage level (IND, ESP, ECH, E1D, FAM), start, end, reason, gap before |
| `plan` | plan design | deductible, out-of-pocket maximum, coinsurance, copays, inpatient copay per day |
| `provider` | provider | NPI, entity type, specialty, network status, billing group, facility flag, pharmacies |
| `medical_claim` | claim *version* | root and version, frequency code, type, status, dates, place of service, admission and discharge, DRG, billing, rendering, facility and attending NPI, billed, allowed, paid, copay, coinsurance, deductible, COB, denial CARC and RARC, prior authorization |
| `medical_claim_line` | line | code system and code, modifiers, units, revenue code, diagnosis pointers, amounts, line status |
| `claim_diagnosis` | diagnosis | ordered ICD-10-CM with present-on-admission |
| `claim_procedure` | procedure | ICD-10-PCS |
| `prior_authorization` | authorization | request, decision, status, validity, retroactive flag |
| `pharmacy_claim` | transaction | NDC, written and fill date, quantity, days supply, refill number, DAW, prescriber, pharmacy, retail or mail, tier, costs, status, reject code |
| `rx_order` | prescription | written date, refills, prescriber, indication and the diagnosis behind it |
| `drug_reference` | NDC used | name, strength, form, route, class, schedule, brand or generic, marketing dates |
| `rx_adherence` | member, year, group | proportion of days covered, gaps, early refills, abandonment |
| `member_risk` | member, year | HCC categories and RAF, prospective and concurrent |
| `member_accumulator` | member (and family), plan year | deductible and out-of-pocket met, limits |

**Money rules.** On every claim and line, `allowed = paid + member responsibility` to the cent, where
member responsibility is copay + coinsurance + deductible. When another payer paid first (COB),
`allowed` is what remains after that payment, `cob_paid_amount` is what the other payer paid and
`gross_allowed_amount` the contracted total. Voids are the exact negation of the claim they undo.

**Claim lifecycle.** An original claim (frequency 1) is submitted and adjudicated; it is paid or
denied with a CARC (and RARC); a denied claim may be corrected and resubmitted (frequency 7, a new
`claim_id` with `original_claim_id`); a paid claim may later be adjusted (replacement) or voided
(frequency 8, negative amounts) and re-billed; exact duplicates arrive and are denied (CARC 18, RARC
N522); services that need prior authorization are billed with one, or deny (CARC 197) and are
resubmitted with a retroactive one; out-of-network HMO claims deny (CARC 242). Accumulators are
computed in service-date order on the claims that end up paid.

## 5. Code sets, licences and bring-your-own tables

| Asset | Source | Shipped here | Notes |
|---|---|---|---|
| ICD-10-CM | CDC/NCHS | curated seed subset (about 250 codes with effective and end dates and age and sex edits) | the October changes are modelled and tested (`N18.3` to `N18.30`, `M54.5` to `M54.50`, `R05` to `R05.9`, `F32.A`, `E66.81x`); the codes lane's full release history validates it |
| ICD-10-PCS | CMS | 17 inpatient procedure codes | validated against the codes lane's asset |
| HCPCS Level II | CMS | the open set used by the services (about 60 codes) | CPT is **never** shipped |
| CPT | AMA | **no** | lines for CPT-only services carry a Shape service key (`SHAPE-SVC`); a licensed table (`table,key,value` CSV, `--cpt-table`) puts the CPT code on the line |
| Revenue codes, type of bill | NUBC | **no** | the same bring-your-own table (`revenue`, `tob`); empty otherwise |
| NUCC taxonomy | NUCC (AMA) | **no** | bring-your-own (`taxonomy`); the codes lane found a licence to be needed for commercial use |
| CARC and RARC | X12 | codes only, our own short labels | the official text is copyrighted: bring your own |
| MS-DRG | CMS | titles, approximate weights and geometric mean stays for 43 DRGs | `[VERIFY]` against the CMS Table 5 of the year |
| NDC | FDA | interim synthetic directory by default; the **FDA directory** through the codes lane's `ndc` asset | see section 7 |
| HCC | CMS | 45 ICD-10-CM to V28 mappings with **illustrative** weights | `[VERIFY]`: not the CMS relative factors |

## 6. Identifiers that cannot be real

NPIs are 10 digits with a valid Luhn check digit and a leading `99` (CMS issued NPIs beginning 1 or 2;
the codes lane's rule); member ids are `SYN` + digits and subscriber ids `SYS` + digits; SSNs have
area 900-999 (never issued); e-mail uses `example.com`, `.org` and `.net`; phones use `555-01xx`;
provider tax ids start `00`; pharmacy NCPDP ids start `9`. Names are random combinations of common
given names and surnames and can coincide with real people by chance: they are not identifiers.

## 7. Reference data and the codes lane

The generator draws from its own seed sets so it runs offline. With
`sqllocks-shape-healthcare-codes` installed and assets built (`shape healthcare-codes fetch ...`):

```python
from shape_domains.healthcare_payer.codes_adapter import AssetCodes, FdaNdcDirectory
codes = AssetCodes.load()
codes.cross_check_seeds()                  # [] when every seed code agrees with the assets
codes.validate_tables(data.tables)         # item 5 on the real ICD-10-CM history, PCS, HCPCS, NDC
data = generate(5000, ndc=FdaNdcDirectory(codes.ndc.table))   # real, marketed NDCs
```

## 7a. Calibration

Every probability the simulation draws is in one table (`calibration.RATES`) with its unit, public
source and status. **cited `[VERIFY]`** means the figure is a rounded value from the named
publication as known when the table was written and was not re-fetched: a reviewer should confirm
it. **assumption** is a modelling choice with no direct published figure; its note names the
aggregate that *is* cited and against which the measured report checks the outcome. The same table
drives the code (a test fails when a model reads a rate that is not listed).

Rates are calibratable from your own data: `calibrate.from_tables(tables)` reads claims, eligibility
and pharmacy tables in this schema and returns a `Calibration` whose overrides replace the public
figures (line-of-business mix, sex mix, denial and reject rates, cesarean share, respiratory
seasonality, coded prevalence by age band); pass it as `generate(..., calibration=...)`.
`tests/healthcare_payer/test_determinism_and_calibration.py` shows it recovers the rates a population
was generated with.

| Rate | Value | Unit | Source | Status |
|---|---|---|---|---|
| `pop.lob_mix` | commercial: 0.55, ma: 0.2, medicaid: 0.25 | share of members | KFF Health Insurance Coverage of the Total Population (employer 54%, Medicaid/CHIP 20%, Medicare 18%). configuration default for a mixed payer; set per run | assumption |
| `pop.female_share` | commercial: 0.51, ma: 0.56, medicaid: 0.56 | share female | US Census ACS 2022 health insurance by sex; CMS Medicare Enrollment Dashboard; MACPAC enrollment. | cited `[VERIFY]` |
| `pop.age_commercial_subscriber` | 0-17: 0, 18-34: 0.22, 35-44: 0.26, 45-54: 0.27, 55-64: 0.25, 65-74: 0, 75+: 0 | share of subscribers | US Census ACS 2022 employed population by age; BLS Current Population Survey. subscribers are working-age adults; 65+ commercial members are not generated | assumption |
| `pop.age_medicaid_adult` | 0-17: 0, 18-34: 0.38, 35-44: 0.22, 45-54: 0.18, 55-64: 0.14, 65-74: 0.05, 75+: 0.03 | share of adult members | MACPAC Medicaid and CHIP enrollment by age; KFF Medicaid enrollment by age. | assumption |
| `pop.medicaid_child_share` | 0.43 | share of Medicaid members under 18 | MACPAC MACStats (children are about 40-45% of enrollees). | cited `[VERIFY]` |
| `pop.ma_age` | 65-69: 0.28, 70-74: 0.27, 75-79: 0.2, 80-84: 0.13, 85+: 0.12 | share of MA members 65+ | CMS Medicare Enrollment Dashboard / KFF Medicare Advantage Enrollment Update. | cited `[VERIFY]` |
| `pop.ma_disabled_share` | 0.1 | share of MA members under 65 (disability entitlement) | KFF Medicare Advantage Enrollment Update (about 10% of beneficiaries are under 65). | cited `[VERIFY]` |
| `pop.spouse_prob` | 0.55 | share of commercial subscribers with a spouse on the plan | KFF Employer Health Benefits Survey (family coverage about 45% of covered workers); US Census ACS. | assumption |
| `pop.child_count` | 0: 0.45, 1: 0.22, 2: 0.22, 3: 0.11 | children per subscriber with dependents | US Census Families and Living Arrangements (average 1.9 children in families with children). | assumption |
| `elig.annual_term` | commercial: 0.16, ma: 0.06, medicaid: 0.28 | annual disenrolment probability | KFF/Commonwealth Fund coverage churn studies; MACPAC Medicaid churn; CMS MA disenrolment (about 5-10% a year). | assumption |
| `elig.reenroll_prob` | commercial: 0.35, ma: 0.1, medicaid: 0.55 | share of terminated members who return | MACPAC Medicaid churn (about 1/3 to 1/2 return within a year). | assumption |
| `elig.gap_days` | commercial: 30–180, ma: 30–120, medicaid: 30–240 | gap length range, days | MACPAC / Urban Institute churn studies. | assumption |
| `elig.cob_prob` | commercial: 0.045, ma: 0.02, medicaid: 0.06 | share of members with other coverage | CMS COB&R / NAIC; Census ACS multiple coverage (about 4-5% commercial). | assumption |
| `elig.dual_share_medicaid_65` | 0.6 | Medicaid members aged 65+ that are also Medicare (dually eligible) | MACPAC Dually eligible beneficiaries data book. | assumption |
| `cond.obesity` | 0-17: 0.19, 18-34: 0.36, 35-44: 0.42, 45-54: 0.45, 55-64: 0.44, 65-74: 0.41, 75+: 0.35 | prevalence | CDC/NCHS NHANES 2017-March 2020 (children 19.7%, adults 41.9%). | cited `[VERIFY]` |
| `cond.obesity_coded` | 0.45 | share of obese members with an obesity code on a claim | published claims studies (obesity under-coded; 30-50%). | assumption |
| `cond.htn` | 0-17: 0.01, 18-34: 0.06, 35-44: 0.15, 45-54: 0.3, 55-64: 0.46, 65-74: 0.6, 75+: 0.72 | diagnosed prevalence | CDC/NCHS NHANES 2017-2020 hypertension prevalence by age (22% 18-39, 55% 40-59, 75% 60+), diagnosed share about 70%. | cited `[VERIFY]` |
| `cond.dm` | 0-17: 0.003, 18-34: 0.02, 35-44: 0.05, 45-54: 0.1, 55-64: 0.15, 65-74: 0.21, 75+: 0.24 | diagnosed diabetes prevalence | CDC National Diabetes Statistics Report 2023 (diagnosed 8.9% of adults; 3% 18-44, 12% 45-64, 17-21% 65+). | cited `[VERIFY]` |
| `cond.dm1_share` | child: 0.95, adult: 0.07 | share of diabetes that is type 1 | CDC National Diabetes Statistics Report (T1D about 5-10% of adult diabetes; most youth diabetes). | cited `[VERIFY]` |
| `cond.lipid` | 0-17: 0.01, 18-34: 0.05, 35-44: 0.12, 45-54: 0.25, 55-64: 0.38, 65-74: 0.5, 75+: 0.48 | diagnosed lipid disorder prevalence | CDC/NCHS NHANES 2017-2020 (high total cholesterol 11.5%; claims prevalence of dyslipidemia 30%+ over 45). | assumption |
| `cond.ckd` | 0-17: 0, 18-34: 0.004, 35-44: 0.006, 45-54: 0.02, 55-64: 0.04, 65-74: 0.1, 75+: 0.18 | diagnosed CKD prevalence | CDC CKD Surveillance System 2023 (14% of adults; about 90% of stage 1-3 unaware; 36% of 65+ by labs). | assumption |
| `cond.asthma` | 0-17: 0.065, 18-34: 0.08, 35-44: 0.077, 45-54: 0.077, 55-64: 0.075, 65-74: 0.07, 75+: 0.065 | current asthma prevalence | CDC/NCHS NHIS 2021 (adults 7.7%, children 6.5%). | cited `[VERIFY]` |
| `cond.copd` | 0-17: 0, 18-34: 0, 35-44: 0, 45-54: 0.025, 55-64: 0.06, 65-74: 0.1, 75+: 0.12 | prevalence | CDC BRFSS COPD prevalence (6.4% of adults, about 11-12% over 65). | cited `[VERIFY]` |
| `cond.depression` | 0-17: 0.06, 18-34: 0.09, 35-44: 0.09, 45-54: 0.09, 55-64: 0.08, 65-74: 0.06, 75+: 0.06 | coded prevalence | SAMHSA NSDUH 2021 major depressive episode (8.3% of adults, 20% of adolescents) times a coded share about 0.5-1. | assumption |
| `cond.anxiety` | 0-17: 0.03, 18-34: 0.09, 35-44: 0.08, 45-54: 0.07, 55-64: 0.06, 65-74: 0.05, 75+: 0.05 | coded prevalence | NIMH anxiety disorders (19% of adults in a year) and claims-coded prevalence (about 7-9%). | assumption |
| `cond.adhd` | 0-17: 0.07, 18-34: 0.04, 35-44: 0.03, 45-54: 0.02, 55-64: 0.01, 65-74: 0, 75+: 0 | diagnosed prevalence (age >= 3) | CDC/NCHS / CDC ADHD (children 3-17: 9.8%; adults 4.4%). | cited `[VERIFY]` |
| `cond.sud_opioid` | 0-17: 0, 18-34: 0.007, 35-44: 0.007, 45-54: 0.006, 55-64: 0.004, 65-74: 0.002, 75+: 0.001 | coded prevalence | SAMHSA NSDUH 2021 opioid use disorder (0.7% of adults 12+). | cited `[VERIFY]` |
| `cond.sud_alcohol` | 0-17: 0, 18-34: 0.012, 35-44: 0.014, 45-54: 0.014, 55-64: 0.012, 65-74: 0.006, 75+: 0.003 | coded prevalence | SAMHSA NSDUH 2021 alcohol use disorder (10.6% of 12+; moderate-severe and treated is a small share). | assumption |
| `cond.bipolar` | 0.007 | coded prevalence, age >= 15 | NIMH bipolar disorder (2.8% of adults, 4.4% lifetime); coded 0.5-1%. | assumption |
| `cond.schizophrenia` | 0.004 | coded prevalence, age >= 15 | NIMH schizophrenia (about 0.25-0.64%). | cited `[VERIFY]` |
| `cond.hypothyroid` | F: 0.08, M: 0.02 | adult prevalence by sex | American Thyroid Association general information (hypothyroidism about 5% of US adults, women 5-8x men). | cited `[VERIFY]` |
| `cond.gerd` | 0.12 | adult diagnosed prevalence | ACG GERD guideline (about 20% symptomatic weekly; diagnosed about 10-15%). | assumption |
| `cond.autoimmune` | 0.01 | diagnosed prevalence of rheumatoid arthritis, psoriasis and inflammatory bowel disease treated by a specialist, age >= 18 | Arthritis Foundation / CDC (RA 0.6-1%), NPF (psoriasis 3%, a third treated), CCFA (IBD 0.7%); about 0.7-1.2% of commercial members use a specialty biologic (IQVIA). | assumption |
| `cond.afib` | 0-17: 0, 18-34: 0.002, 35-44: 0.004, 45-54: 0.01, 55-64: 0.03, 65-74: 0.08, 75+: 0.12 | prevalence | CDC atrial fibrillation fact sheet (2.7-6.1 million; 9% at 65+). | cited `[VERIFY]` |
| `cond.hf` | 0-17: 0, 18-34: 0.001, 35-44: 0.003, 45-54: 0.01, 55-64: 0.025, 65-74: 0.06, 75+: 0.12 | prevalence | AHA Heart Disease and Stroke Statistics 2023 (heart failure 2.4% of adults; 6.7 million). | cited `[VERIFY]` |
| `cond.cad` | 0-17: 0, 18-34: 0.002, 35-44: 0.01, 45-54: 0.04, 55-64: 0.08, 65-74: 0.14, 75+: 0.2 | prevalence | AHA Heart Disease and Stroke Statistics 2023 (CAD 7.1% of adults; 5% to 25% by age). | assumption |
| `cond.osteoporosis_f` | 0-17: 0, 18-34: 0, 35-44: 0, 45-54: 0.02, 55-64: 0.07, 65-74: 0.15, 75+: 0.25 | female prevalence | NIH/NHANES 2017-2018 osteoporosis (19.6% of women 50+); claims-coded about half. | assumption |
| `cond.bph_m` | 0-17: 0, 18-34: 0, 35-44: 0.01, 45-54: 0.06, 55-64: 0.18, 65-74: 0.3, 75+: 0.38 | male diagnosed BPH | NIDDK BPH statistics (50% of men 51-60, 90% 80+; diagnosed share lower). | assumption |
| `cluster.obesity_or` | htn: 2.2, dm: 2.8, lipid: 1.8, ckd: 1.3, asthma: 1.4, depression: 1.4 | odds ratio vs non-obese | CDC NHANES / published meta-analyses (obesity: HTN OR 2-3, T2D OR 3-7, dyslipidaemia OR 1.5-2.5). | assumption |
| `cluster.dm_or` | htn: 2.5, lipid: 3.6, ckd: 3, cad: 2, hf: 2, depression: 1.6 | odds ratio vs non-diabetic | ADA Standards of Care 2023 (HTN in about 70-75% of adults with diabetes; dyslipidaemia in about 70-80%). | assumption |
| `cluster.htn_or` | ckd: 2.2, cad: 2, hf: 2.5, afib: 1.8 | odds ratio vs normotensive | AHA 2017 ACC/AHA hypertension guideline; USRDS 2022. | assumption |
| `target.dm_with_htn` | 0.6–0.85 | P(HTN | diabetes), acceptable band | ADA Standards of Care 2023: about 70% of adults with diabetes have hypertension. | cited `[VERIFY]` |
| `target.dm_with_lipid` | 0.55–0.85 | P(lipid | diabetes), band | ADA Standards of Care 2023: dyslipidaemia in 70-80% of adults with diabetes. | cited `[VERIFY]` |
| `target.dm_with_obesity` | 0.45–0.8 | P(obesity | diabetes), band | CDC/NCHS NHANES: 87% of adults with diabetes are overweight or obese, about 55-60% obese. | cited `[VERIFY]` |
| `target.dm_with_ckd` | 0.05–0.3 | P(CKD diagnosis | diabetes), band | CDC CKD Surveillance 2023 (diabetes in 38% of CKD; about 15% of diabetics diagnosed). | cited `[VERIFY]` |
| `dm.type2_on_insulin` | 0.25 | share of type 2 on insulin | CDC National Diabetes Statistics Report 2023 (insulin use among adults with diabetes about 31%). | cited `[VERIFY]` |
| `dm.metformin_share` | 0.62 | share of type 2 on metformin | published claims studies (metformin first line, 55-70% of treated T2D). | cited `[VERIFY]` |
| `dm.newer_agent_share` | 0.28 | share of type 2 on SGLT2/GLP-1/DPP-4 | published claims studies 2019-2022 (about 25-35%). | assumption |
| `dm.hba1c_per_year` | 2.6 | HbA1c tests per diabetic member-year | ADA Standards of Care (2-4 a year); HEDIS HBD testing rate about 90%. | cited `[VERIFY]` |
| `dm.eye_exam_annual` | 0.58 | annual dilated eye exam | NCQA HEDIS EED (commercial about 55-60%, MA about 65%). | cited `[VERIFY]` |
| `dm.foot_exam_annual` | 0.35 | annual foot exam or podiatry visit | NHIS diabetes care (about 70% self-report; claims-visible about 30-40%). | assumption |
| `dm.uacr_annual` | 0.45 | annual urine albumin-creatinine ratio | NCQA HEDIS KED (about 40-45% 2022). | cited `[VERIFY]` |
| `dm.statin_age_40_75` | 0.62 | statin use in diabetics aged 40-75 | ADA / CDC (about 60% of diabetics over 40 on statins). | cited `[VERIFY]` |
| `dm.complication_hazard` | 0.035 | annual probability of a new complication code | UKPDS / CDC (microvascular complications 2-4% a year in T2D). | assumption |
| `dm.visits_per_year` | 3.4 | PCP/endocrine visits per diabetic member-year | NAMCS 2019 (diabetes visits about 3-4 a year). | assumption |
| `htn.visits_per_year` | 2.2 | visits per hypertensive member-year | NAMCS 2019 (hypertension as a visit reason 2-3 a year). | assumption |
| `htn.treated` | 0.78 | share of diagnosed hypertensives on a drug | CDC NHANES 2017-2020 (about 75-80% of diagnosed on treatment). | cited `[VERIFY]` |
| `lipid.statin_treated` | 0.55 | share of lipid-disorder members on a statin | CDC NCHS Data Brief 2020 (28% of adults 40+ on statins; about half of those with high LDL). | assumption |
| `lipid.panel_per_year` | 0.75 | lipid panel per lipid-disorder member-year | ACC/AHA cholesterol guideline (annual monitoring). | assumption |
| `ckd.progress_hazard` | 1: 0.03, 2: 0.03, 3: 0.06, 4: 0.15, 5: 0.2 | annual stage progression | USRDS 2022 / KDIGO (eGFR decline 1-4 mL/min/yr). | assumption |
| `ckd.nephrology_visits` | 2 | nephrology visits a year, stage >= 3 | KDIGO 2012 monitoring frequency. | assumption |
| `asthma.exac_rate` | 0.4 | exacerbations per asthmatic member-year | CDC NHIS 2021 (about 40% of adults with asthma had an attack in a year). | cited `[VERIFY]` |
| `asthma.exac_ed_share` | 0.18 | exacerbations that go to the ED | CDC National Asthma Data (asthma ED visits 4-5 per 100 asthmatics). | assumption |
| `copd.exac_rate` | 0.85 | exacerbations per COPD member-year | Hurst et al NEJM 2010 ECLIPSE (mean 0.85-1.3 a year). | cited `[VERIFY]` |
| `copd.exac_admit_share` | 0.2 | exacerbations that need admission | HCUP / Medicare (about 20% of COPD exacerbations). | assumption |
| `resp.season_weight` | 1: 1.7, 2: 1.6, 3: 1.2, 4: 0.9, 5: 0.7, 6: 0.6, 7: 0.6, 8: 0.7, 9: 0.9, 10: 1, 11: 1.3, 12: 1.7 | relative monthly weight | CDC FluView ILI surveillance, NAMCS respiratory visit seasonality (winter peak 2-3x summer trough). | assumption |
| `resp.acute_per_year` | 0-17: 1.3, 18-34: 0.4, 35-44: 0.38, 45-54: 0.4, 55-64: 0.42, 65-74: 0.45, 75+: 0.5 | acute respiratory encounters per member-year | NAMCS 2019 (acute upper respiratory infections about 2% of visits). | assumption |
| `resp.flu_share` | 0.12 | share of respiratory episodes coded as influenza | CDC burden estimates 2019-20 (8% of population symptomatic flu). | assumption |
| `resp.covid_wave` | 2022-01: 0.04, 2022-02: 0.02, 2022-07: 0.012, 2022-12: 0.008, 2023-01: 0.008, 2023-08: 0.006, 2024-01: 0.006, 2024-08: 0.005 | monthly COVID-19 coded encounter probability per member | CDC COVID Data Tracker case and visit trends 2022-2024. | assumption |
| `flu_vaccine.rate` | 0-17: 0.5, 18-34: 0.3, 35-44: 0.35, 45-54: 0.4, 55-64: 0.48, 65-74: 0.68, 75+: 0.72 | annual flu vaccination | CDC FluVaxView 2022-23 coverage by age. | cited `[VERIFY]` |
| `bh.visits_per_year` | depression: 6, anxiety: 5, adhd: 3, sud_opioid: 14, sud_alcohol: 8, bipolar: 7, schizophrenia: 8 | visits per treated member-year | SAMHSA / HEDIS follow-up measures; claims studies. | assumption |
| `bh.treated_share` | 0.7 | share of coded behavioural conditions with treatment in the year | NSDUH 2021 (about 50-70% of adults with MDE received treatment). | assumption |
| `bh.admit_per_year` | depression: 0.008, bipolar: 0.05, schizophrenia: 0.1, sud_opioid: 0.04, sud_alcohol: 0.03 | psychiatric or SUD admission per member-year | HCUP NIS mental health and substance use stays. | assumption |
| `preg.births_per_1000` | 15-19: 13.9, 20-24: 56.7, 25-29: 91, 30-34: 95.4, 35-39: 54.5, 40-44: 12 | live births per 1000 women a year | CDC/NCHS NVSS Births: Provisional Data for 2022. | cited `[VERIFY]` |
| `preg.cesarean_rate` | 0.321 | share of deliveries by cesarean | CDC/NCHS NVSS Births: Provisional Data for 2022 (32.1%). | cited `[VERIFY]` |
| `preg.gdm_rate` | 0.08 | gestational diabetes in pregnancy | CDC Diabetes and Pregnancy (2% to 10% of pregnancies; 8.3% of births 2021). | cited `[VERIFY]` |
| `preg.htn_rate` | 0.075 | hypertensive disorder of pregnancy | CDC/NCHS NVSS 2021 (gestational hypertension 7-8% of births). | cited `[VERIFY]` |
| `preg.prenatal_visits` | 12 | prenatal visits per pregnancy | ACOG schedule (about 12-14 visits); CDC PRAMS. | cited `[VERIFY]` |
| `preg.preterm_rate` | 0.104 | preterm birth (earlier delivery, longer stay) | CDC/NCHS NVSS 2022 (10.4%). | cited `[VERIFY]` |
| `preg.high_risk_age` | 35 | age at which advanced maternal age is coded | ACOG Committee Opinion 2022 (35 and older). | cited `[VERIFY]` |
| `cancer.breast_f_incidence` | 35-44: 120, 45-54: 250, 55-64: 330, 65-74: 400, 75+: 380 | per 100k women a year | NCI SEER Cancer Stat Facts: Female Breast Cancer 2016-2020. | cited `[VERIFY]` |
| `cancer.prostate_incidence` | 45-54: 60, 55-64: 400, 65-74: 780, 75+: 560 | per 100k men a year | NCI SEER Cancer Stat Facts: Prostate Cancer 2016-2020. | cited `[VERIFY]` |
| `cancer.colorectal_incidence` | 35-44: 18, 45-54: 55, 55-64: 85, 65-74: 160, 75+: 230 | per 100k a year | NCI SEER Cancer Stat Facts: Colorectal Cancer 2016-2020. | cited `[VERIFY]` |
| `cancer.lung_incidence` | 45-54: 22, 55-64: 85, 65-74: 230, 75+: 260 | per 100k a year | NCI SEER Cancer Stat Facts: Lung and Bronchus Cancer 2016-2020. | cited `[VERIFY]` |
| `cancer.chemo_share` | breast: 0.45, prostate: 0.1, colon: 0.5, lung: 0.65 | share treated with systemic therapy | NCI SEER-Medicare treatment patterns. | assumption |
| `cancer.surgery_share` | breast: 0.88, prostate: 0.4, colon: 0.85, lung: 0.3 | share having cancer surgery | NCI SEER treatment statistics. | assumption |
| `prev.wellness_visit` | commercial: 0.5, ma: 0.68, medicaid: 0.45 | annual preventive visit by plan | NCQA HEDIS WCV/AAP, CMS AWV utilisation (about 40% of Medicare). | assumption |
| `prev.mammogram_biennial` | 0.74 | women 50-74 screened in 2 years | NCQA HEDIS BCS-E (commercial about 75%). | cited `[VERIFY]` |
| `prev.colorectal_10y` | 0.68 | adults 45-75 up to date | NCQA HEDIS COL-E (commercial about 66-70%). | cited `[VERIFY]` |
| `prev.cervical_3y` | 0.74 | women 21-64 screened | NCQA HEDIS CCS (about 74%). | cited `[VERIFY]` |
| `prev.psa_screen_annual` | 0.12 | men 55-69 screened per year | CDC NHIS 2018 (about 35% screened in the past 12 months age 50+ in older cohorts; falling). | assumption |
| `util.acute_visit_per_year` | 0-17: 1.2, 18-34: 0.7, 35-44: 0.8, 45-54: 0.9, 55-64: 1, 65-74: 1.2, 75+: 1.4 | non-respiratory acute office visits | NAMCS 2019 (3.0 office visits per person a year, about a third acute). | assumption |
| `util.ed_per_1000` | commercial: 95, ma: 230, medicaid: 430 | background (injury, pain, symptom) ED visits per 1000 a year | NCHS NHAMCS 2019/2021 ED rates by insurance (Medicaid about 3x private). condition-driven ED visits are added on top; the measured total is checked against ED targets | assumption |
| `util.ed_total_per_1000` | commercial: 120–260, ma: 260–520, medicaid: 330–760 | acceptable total ED visits per 1000 a year | NCHS NHAMCS 2021; HCCI Health Care Cost and Utilization Report (commercial about 140-160). | cited `[VERIFY]` |
| `util.admit_total_per_1000` | commercial: 30–85, ma: 150–330, medicaid: 40–130 | acceptable total inpatient admissions per 1000 a year | HCCI HCCUR 2021 (commercial about 55-65); MedPAC Data Book (Medicare about 250-300); KFF Medicaid. | cited `[VERIFY]` |
| `util.admit_background_per_1000` | 0-17: 4, 18-34: 6, 35-44: 9, 45-54: 14, 55-64: 22, 65-74: 55, 75+: 110 | background medical/surgical admissions per 1000 | HCUP NIS 2020 age-specific discharge rates excluding delivery. | assumption |
| `util.frailty_shape` | 0.3 | gamma shape of the member utilisation multiplier (mean 1) | tuned so the top 5% of members carry about half of spend (MEPS). | assumption |
| `util.top5_share` | 0.38–0.62 | acceptable share of total allowed spend by the top 5% of members | AHRQ MEPS Statistical Brief 2021 (top 5% about 50%, top 1% about 22%). | cited `[VERIFY]` |
| `util.top1_share` | 0.14–0.36 | acceptable share by the top 1% | AHRQ MEPS Statistical Brief 2021 (about 22%). | cited `[VERIFY]` |
| `util.readmit_30d` | 0–0.15 | acceptable 30-day all-cause readmission among admissions | CMS HRRP / HCUP (all-cause 30-day about 14% Medicare, 8-10% commercial). | cited `[VERIFY]` |
| `util.readmit_prob` | commercial: 0.05, ma: 0.11, medicaid: 0.08 | probability a stay is followed by a 30-day readmission | HCUP Statistical Brief 248 (30-day readmission about 14% all payers, higher in older). | assumption |
| `util.death_in_hospital_share` | 0.4 | deaths that occur in an inpatient stay | CDC NCHS deaths by place of death (about 30% hospital). | assumption |
| `mortality.annual` | 0-17: 0.0003, 18-34: 0.001, 35-44: 0.0026, 45-54: 0.0055, 55-64: 0.012, 65-74: 0.025, 75+: 0.075 | all-cause annual probability of death | CDC NCHS United States Life Tables 2021 (insured populations are healthier; scale 0.8). | cited `[VERIFY]` |
| `mortality.insured_scale` | 0.8 | multiplier for an insured population | NCHS life tables vs insured cohort studies. | assumption |
| `price.payer_multiplier` | commercial_pro: 1.4, commercial_facility: 2.4, commercial_lab: 1.8, ma: 1, medicaid: 0.72 | allowed as a multiple of the Medicare-like base | RAND Hospital Price Transparency Study (commercial 224% of Medicare overall; professional about 140%); Urban Institute Medicaid-to-Medicare fee index (72%). | cited `[VERIFY]` |
| `price.region_factor` | high: 1.12, mid: 1, low: 0.92 | regional price factor | CMS PFS GPCI range (0.87-1.20 combined). | assumption |
| `price.noise_sigma` | 0.08 | lognormal sigma of provider contracted-rate variation | FAIR Health / Health Care Cost Institute price variation within a market. | assumption |
| `price.billed_markup` | commercial: 3, ma: 2.5, medicaid: 2.2 | billed charge as a multiple of allowed | Medicare cost report charge-to-cost ratios; HCCI charge markups. | assumption |
| `price.drg_base_rate` | 7800 | Medicare-like base rate per DRG weight 1.0, USD (operating, capital and typical add-ons) | CMS IPPS FY2023 national standardised amount (about $6.5-7k) plus capital and IME/DSH add-ons (about 15-20%). | cited `[VERIFY]` |
| `price.drg_case_sigma` | 0.5 | lognormal sigma of the allowed amount within a DRG (case mix, implants, ICU days) | HCUP cost variation within MS-DRG (coefficient of variation about 0.5-1.0 of case cost). | assumption |
| `price.outlier_per_diem` | 700 | Medicare-like outlier per-diem for days beyond twice the DRG geometric mean stay, USD | CMS IPPS cost-outlier policy (marginal cost factor 80% of costs above the fixed-loss threshold). | assumption |
| `plan.oop_max_commercial` | 2022: 8700, 2023: 9100, 2024: 9450 | ACA out-of-pocket limit, individual, USD | HealthCare.gov / CMS Notice of Benefit and Payment Parameters. | cited `[VERIFY]` |
| `plan.moop_ma` | 2022: 7550, 2023: 8300, 2024: 8850 | MA in-network MOOP limit, USD | CMS Medicare Advantage rate announcements 2022-2024. | cited `[VERIFY]` |
| `plan.hsa_min_deductible` | 2022: 1400, 2023: 1500, 2024: 1600 | HDHP minimum deductible, individual, USD | IRS Rev. Proc. 2021-25, 2022-24, 2023-23. | cited `[VERIFY]` |
| `net.in_network_share` | commercial: 0.92, ma: 0.95, medicaid: 0.97 | share of providers/claims in network | KFF Employer Health Benefits Survey; Kaiser analysis of OON billing (about 5-10%). | assumption |
| `claim.initial_denial` | commercial: 0.08, ma: 0.07, medicaid: 0.11 | share of claims denied on first adjudication | Premier Inc 2022 and Change Healthcare Denials Index (about 10-12% initial denial); KFF MA/ACA denial data (about 7-19%). | assumption |
| `claim.resubmit_share` | 0.45 | share of denied claims corrected and resubmitted | MGMA/AHA denial management surveys (about 45-65% reworked). | assumption |
| `claim.resubmit_paid_share` | 0.6 | share of resubmissions that are paid | HFMA denial recovery studies (about 60-65% overturned). | assumption |
| `claim.adjust_share` | 0.02 | paid claims later replaced (late adjustment) | payer operations benchmarks (1-3%). | assumption |
| `claim.reversal_share` | 0.006 | paid claims voided | payer operations benchmarks (0.3-1%). | assumption |
| `claim.duplicate_share` | 0.008 | claims re-sent as exact duplicates | CAQH Index 2022 (duplicate submissions below 1%). | assumption |
| `claim.lag_days` | 7–45 | days from service to receipt (range) | CMS timely filing practice; payer benchmarks. | assumption |
| `claim.pa_missing` | 0.04 | prior-authorisation-required services billed without one | AMA prior authorisation survey 2022; CMS MA prior-auth data. | assumption |
| `rx.primary_nonadherence` | generic: 0.07, brand: 0.16 | new prescriptions never filled | Fischer et al, J Gen Intern Med 2010 (24% of e-prescriptions never filled); recent PBM data (about 10-15%). | assumption |
| `rx.monthly_discontinue` | statin: 0.045, antihypertensive: 0.03, antidiabetic: 0.03, inhaler: 0.04, antidepressant: 0.055, other: 0.035 | monthly probability of abandoning a maintenance drug | published persistence studies (1-year statin persistence about 50-60%; antihypertensives 60-75%). | assumption |
| `rx.pdc_target` | 0.6 | share of maintenance users with PDC >= 80% (acceptable band 0.40-0.80) | CMS Part D Star Ratings adherence measures (national averages 80-88% for statin/RAS/diabetes among continuous users); commercial about 55-65% of new users. | assumption |
| `rx.early_refill` | 0.05 | refills made 20% or more early | NCPDP refill-too-soon reject data (2-6%). | assumption |
| `rx.reject_rate` | commercial: 0.045, ma: 0.05, medicaid: 0.06 | share of submissions rejected | NCPDP/Surescripts and PBM benchmarks (about 5% of claims reject). | assumption |
| `rx.reversal_rate` | 0.025 | paid fills reversed (not picked up) | NCPDP/Surescripts (about 2-4% returned to stock). | cited `[VERIFY]` |
| `rx.mail_share` | commercial: 0.18, ma: 0.22, medicaid: 0.02 | maintenance fills via mail order | PCMA/IQVIA (mail order about 10-15% of retail scripts; 30%+ of maintenance for some plans). | assumption |
| `rx.generic_dispense_rate` | 0.9 | generic share of dispensed fills | IQVIA Use of Medicines in the US 2022 (about 90%). | cited `[VERIFY]` |
| `rx.copay_tier` | commercial: (10, 35, 70, 0.25), ma: (5, 15, 47, 0.25), medicaid: (1, 2, 4, 0) | copay tier 1, 2, 3 USD and tier 4 coinsurance | KFF Employer Health Benefits Survey 2022 (average $11/$33/$58); CMS Part D benefit parameters; state Medicaid copay rules. | assumption |
| `rx.dispensing_fee` | commercial: 1.5, ma: 1.3, medicaid: 10 | dispensing fee, USD | CMS Medicaid dispensing fee survey (about $10); PBM contracts. | assumption |
| `rx.written_to_fill_days` | 0–3 | days between written and first fill | Surescripts/PBM first-fill timing. | assumption |
| `rx.adherence_beta` | antidiabetic: 5–1.6, statin: 4.6–1.7, antihypertensive: 5–1.6, inhaler: 3.2–1.8, antidepressant: 3.6–1.9, other: 4.5–1.7 | Beta(a, b) of a member's refill adherence by drug group | calibrated so the share of users with PDC >= 80% falls in the CMS Star Ratings / published range (rx.pdc_target). | assumption |
| `rx.renew_prob` | 0.92 | probability an expired prescription is renewed | PBM persistence studies. | assumption |
| `rx.pa_first_reject` | 0.45 | first fill of a prior-authorisation drug rejected for PA (code 75) | CoverMyMeds Medication Access Report 2022. | assumption |
| `rx.pa_approved` | 0.85 | PA requests approved | CoverMyMeds Medication Access Report 2022 (about 80-90%). | assumption |
| `rx.ingredient_markup_brand_awp` | 1 | ingredient cost multiplier on the catalogue price (all drugs) | model unit. | assumption |

## 8. Acceptance evidence

All measured with `shape healthcare-payer quality --members 3000 --seed 21 --ndc fda` (native
engine, three simulated years 2022-2024, real FDA NDC directory from the codes lane). Every number
below is produced by `quality.py` from the generated tables, never typed in. The same command
with `--engine behavior` gives the second table.

### Native engine

| # | Acceptance item | Result | Measured |
|---|---|---|---|
| 1 | age and sex edits hold | pass | `{'diagnoses_checked': 136716, 'diagnosis_violations': 0, 'service_violations': 0}` |
| 2 | diagnosis, procedure and drug agree | pass | `{'paid_fills': 46513, 'order_indication_mismatches': 0, 'fills_without_supporting_dx': 0, 'unsupported_by_indication': {}, 'diabetic_members': 291, 'diabetic_with_antidiabetic_fill': 0.825, 'diabetic_with_hba1c': 0.959, 'diabetic_with_eye_exam': 0.804, 'diabetic_with_foot_exam': 0.574, 'hba1c_per_diabetic_member_year': 1.9, 'hba1c_per_year_target': 2.6}` |
| 3 | comorbidity clustering and persistence | pass | `{'diabetic_member_years': 610, 'diabetic_adults': 304, 'P(.|diabetes) simulated problem list': {'htn': 0.694, 'lipid': 0.602, 'obesity': 0.582, 'ckd': 0.207}, 'P(.|diabetes) coded on a claim (same adults)': {'htn': 0.694, 'lipid': 0.592, 'obesity': 0.289, 'ckd': 0.204}, 'P(.|diabetes) coded on claims (member-years)': {'htn': 0.725, 'lipid': 0.626, 'obesity': 0.295, 'ckd': 0.197}, 'persistence(next-year recurrence, n)': {'diabetes': (1.0, 362), 'hypertension': (0.974, 990), 'lipid': (0.962, 765)}, 'targets': {'htn': (0.6, 0.85), 'lipid': (0.55, 0.85), 'obesity': (0.45, 0.8), 'ckd': (0.05, 0.3)}}` |
| 4 | utilisation shape | pass | `{'top5_share': 0.42, 'top1_share': 0.166, 'bottom50_share': 0.059, 'member_years': {'medicaid': 1661, 'ma': 1605, 'commercial': 3620}, 'per_1000_member_years': {'medicaid': {'ed': 392, 'admits': 68}, 'ma': {'ed': 371, 'admits': 173}, 'commercial': {'ed': 129, 'admits': 51}}, 'winter_to_summer_respiratory': 2.58, 'births_per_1000_women_15_44': 58.4, 'cesarean_share': 0.429, 'service_mix_by_age': {'0-17': {'outpatient': 5582, 'ed': 212, 'inpatient': 96}, '18-34': {'outpatient': 6522, 'ed': 220, 'inpatient': 70}, '35-44': {'outpatient': 4424, 'ed': 215, 'inpatient': 43}, '45-54': {'outpatient': 5241, 'inpatient': 21, 'ed': 214}, '55-64': {'outpatient': 5241, 'ed': 131, 'inpatient': 40}, '65-74': {'outpatient': 7809, 'ed': 197, 'inpatient': 151}, '75+': {'outpatient': 6835, 'inpatient': 153, 'ed': 186}}, 'allowed_per_member_year': 2635, 'allowed_per_member_year_by_lob': {'ma': 4492, 'medicaid': 1302, 'commercial': 2424}, 'targets': {'top5': (0.38, 0.62), 'top1': (0.14, 0.36), 'ed': {'commercial': (120, 260), 'ma': (260, 520), 'medicaid': (330, 760)}, 'admits': {'commercial': (30, 85), 'ma': (150, 330), 'medicaid': (40, 130)}}}` |
| 5 | every code valid and billable on the date of service | pass | `{'diagnoses': 136716, 'invalid_diagnoses': 0, 'invalid_pcs': 0, 'invalid_lines': 0, 'cpt_lines_without_licensed_table': 0, 'fills': 49868, 'ndc_not_marketed_on_fill_date': 0, 'ndc_source': 'FDA NDC Directory (codes lane asset)'}` |
| 6 | financial coherence | pass | `{'claim_rows_not_balancing': 0, 'line_rows_not_balancing': 0, 'header_not_sum_of_lines': 0, 'pharmacy_rows_not_balancing': 0, 'accumulator_rows_over_limit': 0, 'allowed_amount_cv_by_procedure': {'EM_OFFICE_EST_3': 0.264, 'LAB_LIPID_PANEL': 0.351, 'LAB_HBA1C': 0.353}}` |
| 7 | length of stay agrees with DRG; readmissions rare | pass | `{'inpatient_claims': 574, 'los_outside_drg_range': 0, 'discharge_before_admission': 0, 'mcc_drg_without_mcc_dx': 0, 'geometric_mean_los_vs_drg(n>=15)': {'603': (3.5, 3.4, 44), '690': (2.86, 2.9, 39), '807': (2.08, 2.1, 47), '795': (2.33, 2.1, 77), '194': (3.32, 3.6, 19), '788': (2.91, 3.0, 33), '195': (2.63, 2.7, 69), '872': (3.85, 3.6, 47), '192': (3.23, 2.7, 33), '293': (2.78, 2.4, 26), '683': (3.53, 3.2, 15)}, 'readmission_30d_rate': 0.0557, 'band': (0.0, 0.15)}` |
| P | pharmacy dynamics (generic share, adherence, rejects, reversals) | pass | `{'generic_share': 0.875, 'share_pdc_80_or_more': 0.672, 'reject_rate_by_lob': {'ma': 0.0461, 'medicaid': 0.0476, 'commercial': 0.0426}, 'reversal_rate': 0.025, 'mail_share_of_90_day_fills': {'ma': 0.744, 'commercial': 0.598, 'medicaid': 0.072}, 'targets': {'generic': 0.9, 'pdc': 0.6, 'reject': {'commercial': 0.045, 'ma': 0.05, 'medicaid': 0.06}, 'reversal': 0.025}}` |

### Behavior engine (diabetes, hypertension and lipid pathways as documents)

| # | Acceptance item | Result | Measured |
|---|---|---|---|
| 1 | age and sex edits hold | pass | `{'diagnoses_checked': 146366, 'diagnosis_violations': 0, 'service_violations': 0}` |
| 2 | diagnosis, procedure and drug agree | pass | `{'paid_fills': 43725, 'order_indication_mismatches': 0, 'fills_without_supporting_dx': 0, 'unsupported_by_indication': {}, 'diabetic_members': 309, 'diabetic_with_antidiabetic_fill': 0.77, 'diabetic_with_hba1c': 0.922, 'diabetic_with_eye_exam': 0.738, 'diabetic_with_foot_exam': 0.547, 'hba1c_per_diabetic_member_year': 2.38, 'hba1c_per_year_target': 2.6}` |
| 3 | comorbidity clustering and persistence | pass | `{'diabetic_member_years': 623, 'diabetic_adults': 316, 'P(.|diabetes) simulated problem list': {'htn': 0.696, 'lipid': 0.614, 'obesity': 0.579, 'ckd': 0.184}, 'P(.|diabetes) coded on a claim (same adults)': {'htn': 0.696, 'lipid': 0.598, 'obesity': 0.256, 'ckd': 0.18}, 'P(.|diabetes) coded on claims (member-years)': {'htn': 0.73, 'lipid': 0.628, 'obesity': 0.276, 'ckd': 0.178}, 'persistence(next-year recurrence, n)': {'diabetes': (1.0, 367), 'hypertension': (0.985, 1035), 'lipid': (0.976, 788)}, 'targets': {'htn': (0.6, 0.85), 'lipid': (0.55, 0.85), 'obesity': (0.45, 0.8), 'ckd': (0.05, 0.3)}}` |
| 4 | utilisation shape | pass | `{'top5_share': 0.422, 'top1_share': 0.175, 'bottom50_share': 0.06, 'member_years': {'medicaid': 1666, 'ma': 1608, 'commercial': 3611}, 'per_1000_member_years': {'medicaid': {'ed': 390, 'admits': 72}, 'ma': {'ed': 384, 'admits': 173}, 'commercial': {'ed': 124, 'admits': 47}}, 'winter_to_summer_respiratory': 2.97, 'births_per_1000_women_15_44': 60.5, 'cesarean_share': 0.299, 'service_mix_by_age': {'0-17': {'outpatient': 5547, 'ed': 200, 'inpatient': 94}, '18-34': {'outpatient': 6218, 'inpatient': 83, 'ed': 218}, '35-44': {'outpatient': 4789, 'ed': 194, 'inpatient': 37}, '45-54': {'outpatient': 5591, 'ed': 205, 'inpatient': 17}, '55-64': {'outpatient': 5745, 'ed': 151, 'inpatient': 37}, '65-74': {'outpatient': 8359, 'inpatient': 135, 'ed': 228}, '75+': {'outpatient': 7158, 'ed': 202, 'inpatient': 165}}, 'allowed_per_member_year': 2589, 'allowed_per_member_year_by_lob': {'commercial': 2473, 'medicaid': 1241, 'ma': 4248}, 'targets': {'top5': (0.38, 0.62), 'top1': (0.14, 0.36), 'ed': {'commercial': (120, 260), 'ma': (260, 520), 'medicaid': (330, 760)}, 'admits': {'commercial': (30, 85), 'ma': (150, 330), 'medicaid': (40, 130)}}}` |
| 5 | every code valid and billable on the date of service | pass | `{'diagnoses': 146366, 'invalid_diagnoses': 0, 'invalid_pcs': 0, 'invalid_lines': 0, 'cpt_lines_without_licensed_table': 0, 'fills': 46796, 'ndc_not_marketed_on_fill_date': 0, 'ndc_source': 'FDA NDC Directory (codes lane asset)'}` |
| 6 | financial coherence | pass | `{'claim_rows_not_balancing': 0, 'line_rows_not_balancing': 0, 'header_not_sum_of_lines': 0, 'pharmacy_rows_not_balancing': 0, 'accumulator_rows_over_limit': 0, 'allowed_amount_cv_by_procedure': {'EM_OFFICE_EST_3': 0.258, 'LAB_LIPID_PANEL': 0.345, 'LAB_HBA1C': 0.354}}` |
| 7 | length of stay agrees with DRG; readmissions rare | pass | `{'inpatient_claims': 568, 'los_outside_drg_range': 0, 'discharge_before_admission': 0, 'mcc_drg_without_mcc_dx': 0, 'geometric_mean_los_vs_drg(n>=15)': {'872': (4.31, 3.6, 32), '195': (2.82, 2.7, 51), '192': (2.96, 2.7, 26), '690': (2.85, 2.9, 56), '807': (1.83, 2.1, 58), '795': (2.23, 2.1, 78), '293': (2.5, 2.4, 31), '603': (3.31, 3.4, 37), '788': (3.35, 3.0, 25), '470': (2.23, 2.3, 17), '683': (3.59, 3.2, 15), '194': (3.55, 3.6, 17)}, 'readmission_30d_rate': 0.0669, 'band': (0.0, 0.15)}` |
| P | pharmacy dynamics (generic share, adherence, rejects, reversals) | pass | `{'generic_share': 0.885, 'share_pdc_80_or_more': 0.671, 'reject_rate_by_lob': {'ma': 0.046, 'commercial': 0.04, 'medicaid': 0.0459}, 'reversal_rate': 0.0246, 'mail_share_of_90_day_fills': {'medicaid': 0.079, 'ma': 0.741, 'commercial': 0.58}, 'targets': {'generic': 0.9, 'pdc': 0.6, 'reject': {'commercial': 0.045, 'ma': 0.05, 'medicaid': 0.06}, 'reversal': 0.025}}` |

### Item by item

| # | Acceptance item | Test or report | Status |
|---|---|---|---|
| 1 | Age and sex edits | `quality.check_age_sex` (measured above); `tests/healthcare_payer/test_acceptance.py`; also validated against the codes lane's CMS Medicare Code Editor edits through `AssetCodes.validate_tables` | pass: 0 violations in about 137,000 diagnoses |
| 2 | Diagnosis, procedure and drug agree | `quality.check_coherence`; every fill has a supporting diagnosis within the coverage span; every `rx_order` indication is a diagnosis on the problem list | pass |
| 3 | Comorbidity clustering and persistence | `quality.check_comorbidity`: P(condition given diabetes) inside the published bands, recurrence in the next year about 0.96 to 1.0 | pass |
| 4 | Utilisation shape | `quality.check_utilization`: top 5% of members carry 0.42 of allowed cost (band 0.38 to 0.62, close to the low edge, see limits), ED and admissions per 1,000 by line of business inside their bands, winter to summer respiratory ratio 2.6 | pass, thin margin on the cost tail |
| 5 | Every code valid on the date of service | `quality.check_codes` and `AssetCodes.validate_tables` against the **full code release history** of the codes lane (ICD-10-CM, ICD-10-PCS, HCPCS, FDA NDC): 0 invalid diagnoses, 0 invalid procedures, 0 HCPCS missing, **0 of 49,868 fills with an NDC not marketed on the fill date** | pass |
| 6 | Financial coherence | `quality.check_financial`: allowed = paid + member responsibility to the cent on every claim, line and fill; headers equal their lines; 0 accumulator rows above the plan maxima | pass |
| 7 | LOS agrees with DRG; readmissions rare | `quality.check_los_drg`: no stay outside its DRG range, geometric mean stays within about 20% of the DRG's, 30-day readmission rate 0.056 | pass |
| 8 | Blinded clinician review | `shape healthcare-payer kit` builds the kit; `kit-score` scores it (exact binomial, pass: at least 3 reviewers, 20 cases each, pooled accuracy at most 0.60 and one-sided p at least 0.05) | **`[VERIFY]` (owner): the kit is produced and the scoring is tested, the review is a human step and has not been done. A kit with no real cases cannot show indistinguishability** |
| 9 | HTML member timeline report | `shape healthcare-payer timeline`; `tests/healthcare_payer/test_report_kit_cli.py` | pass (rendered and checked for structure, not reviewed by a clinician) |

The fills table also gets its own check (`quality.check_pharmacy`: generic share, adherence by PDC,
reject and reversal rates per line of business, mail share of 90-day fills against the calibration
targets), shown as row P.


## 9. Behavior-engine modules

Diabetes, hypertension and lipid disorders also exist as **behavior-engine module documents**
(`behavior_modules.py`, format `shape-behavior/1`, one document per pathway: onset, reviews,
annual exams, medicines). Every probability in them is read from the calibration table.

```bash
shape healthcare-payer generate --members 5000 --engine behavior -o payer/
```

How it runs (`behavior_engine.py`):

1. the native cohort draws who has each condition on day one (the clustered draw is shared, so the
   comorbidity structure is the same), and the result is written into the behavior entities'
   attributes;
2. `shape_behavior.Simulator` runs the documents for every member at once and returns the event
   table (`condition_onset`, `medication_order`, `visit`, `exam`);
3. `BehaviorPathways` replays each event on the member's own clock through the same clinical
   helpers, so claims, fills, cost sharing and the checks are the same code in both engines.

The documents are the pathway that a captured shape can recalibrate. What they do **not** model,
and the native modules do: diabetes complications, hypoglycaemia and ketoacidosis admissions,
glucose-testing supplies. Pregnancy, cancer, respiratory, behavioral, kidney, cardiac and acute
care run natively in both engines. The behavior run passes the same checks (second table in
section 8); needs the `sqllocks-shape-behavior` plugin; a run is deterministic for a seed.


## 10. Timeline report and review kit

`shape healthcare-payer timeline` writes one self-contained HTML page per member (coverage, problem
list with the diagnoses behind it, risk, then every visit, stay, procedure and fill in date order
with costs and claim outcomes, cost by year, adherence). `shape healthcare-payer kit` writes the
blinded review kit for acceptance item 8 (cases with no identifiers, a scoring sheet, the key, the
instructions, the pass threshold) and `kit-score` scores the filled sheets. The review itself is a
human step: **`[VERIFY]` (owner)**, see section 8.

## 11. Chaos and standards

Drift and chaos mutators for domain faults are not part of this lane. The standards writers (X12 837P
and 837I, 835, 834, FHIR R4, OMOP) are in the standards lane's plugin and read these tables.

## 12. Limits (stated, not hidden)

* **Calibration is partly from memory.** Rows marked cited `[VERIFY]` are rounded figures from the
  named publication as recalled when the table was written; they were not re-fetched. Rows marked
  *assumption* are modelling choices. Before the data is used for anything that depends on a rate,
  have the table reviewed against the sources.
* **Blinded clinician review (item 8) is not done.** `[VERIFY]` (owner).
* **MS-DRG** is a transparent seed of the grouper (43 DRGs, family from the principal diagnosis,
  tier from CC and MCC), with approximate weights and geometric mean stays. It is not the CMS grouper.
* **HCC and RAF** use 45 ICD-10-CM to V28 mappings and *illustrative* weights; they are not the CMS
  relative factors and cannot be used for payment.
* **Cost tail.** The share of cost in the top 5% of members is about 0.42 against a published
  band of 0.38 to 0.62: inside it, near its low edge, and it moves between seeds.
* **Licensed code sets** (CPT, revenue codes, type of bill, NUCC taxonomy) and the official CARC and
  RARC text are not shipped; see section 5.
* **Counties** are null: the bundled ZIP reference has no county column.
* **Interim NDC directory.** Offline, the generator uses a small synthetic directory; with the codes
  lane's assets installed use `--ndc fda` (or `FdaNdcDirectory`) for real, dated packages.
  The FDA listing keeps only products currently listed, so a package withdrawn before the
  listing date is not available to a past fill date.
* **Not under `shape.domains`.** See section 1.
* **HbA1c frequency.** The model gives about 1.9 tests per diabetic member-year (native) against
  the 2.6 in the calibration table (ADA 2 to 4 a year); the check accepts 0.6x to 1.4x.
* **Not a real population.** Names can coincide with real people; identifiers cannot be real
  (section 6); nothing here is derived from real patients.

