# HC-standards: standard healthcare outputs (issue #45, lane `lane/HC-standards`)

Plugin: `plugins/shape-healthcare-standards` (distribution `sqllocks-shape-healthcare-standards`).

## Status

| Piece | State |
|---|---|
| Input table contract | pushed (`shape_healthcare_standards/contract.py`), awaiting HC-domain to match |
| X12 837P / 837I / 835 / 834 | todo |
| FHIR R4 | todo |
| OMOP CDM v5.4 | todo |
| NCPDP | todo |

## Input table contract (for lane `lane/HC-domain`)

Defined in `plugins/shape-healthcare-standards/src/shape_healthcare_standards/contract.py`
(one `Col` per column: name, Arrow type, required flag, meaning). Tables: `member`,
`eligibility`, `provider`, `medical_claim`, `medical_claim_line`, `claim_diagnosis`,
`claim_procedure`, `pharmacy_claim`, `drug_reference`. Extra columns are ignored, a missing
optional column reads as null, a missing required column is an error. **HC-domain: produce
these table and column names, or tell this lane which names differ and the contract will be
changed to match.** Identifiers and codes are strings (NDC 11 digits, ICD-10 without the
decimal point), dates are `date32`, money is `float64`.

At the time of writing `origin/lane/HC-domain` and `origin/lane/HC-codes` carry no work
beyond the shared base, so the code sets are not yet available; the writers copy code values
through and only check their shape (`codes.py`).

## Checks run

(filled in as they are run)
