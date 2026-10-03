# sqllocks-shape-healthcare-codes

Shape plugin: healthcare reference code sets, validators and detectors. It is the code layer
under a domain pack: the domain asks it for codes that are real, valid on the date of service,
and licensed for the way they are used.

* **Code sets**: ICD-10-CM, ICD-10-PCS, HCPCS Level II, FDA NDC, RxNorm (prescribable), CMS place
  of service, CMS-HCC mappings, AHRQ CCSR, and the international ICD-10 family, all behind one
  code model (`CodeSet`). Free sets are built from the official source at a pinned release
  with `shape healthcare-codes fetch`; licensed sets (CPT, SNOMED CT, UB-04 revenue codes and
  type of bill, NUCC taxonomy, CARC/RARC, WHO ICD-10, ICD-10-GM, ICD-10-AM) are
  **bring-your-own**: you supply the file, a loader reads it, and nothing licensed is shipped.
* **Validators**: ICD-10-CM valid and billable on a date of service, NDC valid and marketed on a
  fill date, NPI check digit with a synthetic never-assigned range, age and sex edits.
* **Detectors** (`shape.detectors`): ICD-10, NDC, NPI, HCPCS, CPT, Medicare MBI and member-id
  columns.
* **Wheel size**: the wheel is about 0.7 MB (a 0.6 MB starter subset of ICD-10-CM and the code).
  The full sets are built on your machine with `shape healthcare-codes fetch`.

`THIRD_PARTY_NOTICES.md` records, for every asset, the source URL, release, licence (quoted from
the source's own page, with the date it was read), and whether it is shipped, fetched or
bring-your-own. `docs/plans/lane_status/HC-codes.md` records the checks.

```python
import datetime as dt
from shape_healthcare_codes import load, icd10cm_valid_billable

icd = load("icd10cm")  # after `shape healthcare-codes fetch icd10cm`
icd10cm_valid_billable(icd, "E11.9", dt.date(2026, 10, 2))  # True
icd10cm_valid_billable(icd, "E11", dt.date(2026, 10, 2))  # False: a header, not billable
```

The domain-facing API is documented in `docs/plugins/healthcare-codes.md`.
