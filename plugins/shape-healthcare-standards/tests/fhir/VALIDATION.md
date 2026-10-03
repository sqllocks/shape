# FHIR R4 validation record

Everything below was run in this session on the synthetic `testing.sample_tables()` data.

## Validator

* `validator_cli.jar`, "FHIR Validation tool Version 6.10.4 (Git# 1b90fb13f77b)", built
  2026-09-04, downloaded from
  `https://github.com/hapifhir/org.hl7.fhir.core/releases/latest/download/validator_cli.jar`
  to `~/.cache/shape-hc/validator_cli.jar` (outside the repo). Java: OpenJDK 21.0.11.
* Terminology server `https://tx.fhir.org` was reachable ("Connected to Terminology Server"),
  so code validation ran online; `-tx n/a` was not needed.
* The US Core 6.1.0 package (`hl7.fhir.us.core#6.1.0`) and the CARIN BB 2.1.0 package were
  fetched through the proxy without problems.

## What was validated

The sample tables go through `FhirBundleSink` (one `collection` Bundle file per table, so the
sink code path is what is checked) and, for the payer Organizations that no sink emits,
`payer_organizations()` in a hand-built Bundle. 27 resources in 8 Bundle files: 4 Patient,
4 Coverage, 4 Practitioner, 4 Organization (3 providers + 1 payer), 4 Claim,
4 ExplanationOfBenefit, 3 MedicationDispense.

Commands (the test `test_official_validator.py` runs the second one):

```bash
# base R4 only
java -jar validator_cli.jar bundles/member bundles/eligibility bundles/provider \
  bundles/medical_claim bundles/pharmacy_claim bundles/payer -version 4.0.1 -output base.json

# base R4 + US Core 6.1.0 (+ CARIN BB 2.1.0 for its code systems)
java -jar validator_cli.jar <same directories> -version 4.0.1 \
  -ig hl7.fhir.us.core#6.1.0 -ig hl7.fhir.us.carin-bb#2.1.0 -output ig.json

# the test
SHAPE_FHIR_VALIDATOR_JAR=~/.cache/shape-hc/validator_cli.jar \
  pytest plugins/shape-healthcare-standards/tests/fhir/test_official_validator.py
```

The test result: 1 passed (about 38 s). It is marked `live` (needs the network) and is skipped
when `SHAPE_FHIR_VALIDATOR_JAR` is unset or Java is missing.

## Verdict per resource type (US Core + CARIN BB run)

dom-6 is the "resource should have narrative" best-practice warning on every resource; it is
counted apart because it is the same on all of them.

| Resource type | Count | Errors | Warnings (excl. dom-6) | dom-6 | Information |
| --- | --- | --- | --- | --- | --- |
| Patient | 4 | 0 | 0 | 4 | 0 |
| Coverage | 4 | 0 | 0 | 4 | 0 |
| Practitioner | 4 | 0 | 0 | 4 | 0 |
| Organization | 4 | 0 | 0 | 4 | 5 |
| Claim | 4 | 0 | 10 | 4 | 0 |
| ExplanationOfBenefit | 4 | 0 | 12 | 4 | 0 |
| MedicationDispense | 3 | 2 | 2 | 3 | 0 |

The base-R4-only run (no `-ig`) gives the same errors (MedicationDispense 2) and a few more
warnings for CARIN BB code systems that were not loaded (Claim 14, ExplanationOfBenefit 16).

Patient, Practitioner and Organization carry `meta.profile` for the US Core 6.1.0 profiles
(`us-core-patient`, `us-core-practitioner`, `us-core-organization`) and were validated against
them: no errors. Claim, ExplanationOfBenefit and MedicationDispense declare no profile.

## The 2 remaining errors

Both are `MedicationDispense.medication ... Unknown code '00093726001' in the CodeSystem
'http://hl7.org/fhir/sid/ndc'`. The NDC `00093726001` is the sample data's first drug
(`testing.py`); it is a synthetic number that is not in the NDC directory the terminology
server holds. The mapping is right (system `http://hl7.org/fhir/sid/ndc`, 11-digit code); the
sample value is what the terminology service rejects. The official-validator test therefore
allows exactly this one message for exactly this one code and fails on any other error.
The sample's other NDC (`00071015523`) is a real one and validates (warning: inactive concept).

## Warnings, and why they stay

* `dom-6` narrative: the resources carry no generated HTML narrative; it is a best-practice
  recommendation, and Bulk FHIR data does not carry narrative.
* "A definition for CodeSystem ... could not be found, so the code cannot be validated" for
  `https://www.nubc.org/CodeSystem/RevenueCodes`, `.../TypeOfBill`,
  `.../PriorityTypeOfAdmitOrVisit`, `.../PatientDischargeStatus` (the NUBC systems named in
  the CARIN BB IG), the MS-DRG system, `https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets`
  (HCPCS Level II modifiers such as GP and QW), and the X12 CARC/RARC systems
  (`https://x12.org/codes/claim-adjustment-reason-codes`, `.../remittance-advice-remark-codes`,
  the URIs the task fixed). The terminology server has no definition of these code systems
  (licensed or unpublished), so the code values themselves are not checked. The values are
  the contract's codes copied through.
* `urn:shape:healthcare:ncpdp-reject-code`: FHIR has no registered system for NCPDP reject
  codes; the synthetic URN is the stand-in.
* Organization "does not match any known slice" (information): the US Core Organization
  profile slices identifiers by NPI/CLIA/NAIC; the EIN, NCPDP id and payer id identifiers are
  extra identifiers, which is allowed.
* MedicationDispense: concept `00071015523` "has a status of inactive" in the NDC directory.

## Mapping decisions the validator forced

* Identifier systems are `urn:shape:healthcare:...` URNs: `example.org` URLs are rejected as
  identifier systems.
* Present-on-admission uses the CMS system
  `https://www.cms.gov/Medicare/Medicare-Fee-for-Service-Payment/HospitalAcqCond/Coding`
  (`Y`/`N`/`U`/`W`), not `ex-diagnosis-on-admission` (only yes/no/unknown).
* A rejected pharmacy claim is `cancelled` with the reject code in `statusReason`; R4 4.0.1 has
  no `not-done` (that code exists in R4B).
* NDC and RxNorm codings carry no `display`: the validator checks displays against the
  directories and the dispensed drug name is in `medicationCodeableConcept.text`.
* The CARC/RARC denial reasons are `adjudication` entries with category `noncovered`
  (CARIN BB `C4BBAdjudication`; `denialreason` is not a code there) and `reason` set to the
  CARC or RARC coding.

## Scope and what was not run

* US Core applies to Patient, Practitioner and Organization only. US Core 6.1.0 has no Coverage,
  Claim, ExplanationOfBenefit or MedicationDispense profile, so those were checked against the
  base R4 definitions only (CARIN BB profiles were not claimed or validated).
* Reference targets were checked only inside each Bundle: each Bundle holds one primary table
  and its derived resources, so a reference such as `Patient/SYN100001-00` points to a resource
  that another file holds (or that is not emitted); the validator did not flag these.
* Not run: the validator on the `.ndjson` files themselves (the CLI takes JSON files; the
  NDJSON lines are the same resource dicts the Bundles hold) and on bigger inputs than the
  sample tables.
