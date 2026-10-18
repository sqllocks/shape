# Third-party notices: healthcare code sets

One entry per asset, headed by the asset's title (a test keeps this file and the catalog in
`shape_healthcare_codes.catalog` in step). Every licence statement was read on the source's own
page on **2026-10-02**, and is quoted where the page states one.

Modes:

* **shipped**: a small subset is inside the wheel (only the ICD-10-CM starter subset, 0.6 MB);
* **fetch**: `shape healthcare-codes fetch ASSET` downloads the pinned release from the official
  source on the user's machine and builds the compact Arrow file; nothing is shipped;
* **BYO**: the user supplies the file (`shape healthcare-codes byo`); nothing is downloaded or
  shipped by this plugin.

A statement the source's page does not make is not assumed: the asset is then fetch-only (when it
is a US federal government work) or BYO, and marked `[VERIFY]` where a person should confirm.
Sizes, checksums and counts are in the implementation tests and in each built
asset's manifest (`<asset>.json`).

## ICD-10-CM (CDC/NCHS)

* Source: https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/ (one directory
  per fiscal year; FY2027 is in force from 2026-10-01) and, for the three mid-year updates, the
  CMS files named in `builders/icd10cm.py`.
* Licence: CDC, "Use of Agency Materials", https://www.cdc.gov/other/agencymaterials.html:
  "Most of the information on the CDC and ATSDR websites is not subject to copyright, is in the
  public domain, and may be freely used or reproduced without obtaining copyright permission."
  Attribution is required: "Source: CDC/NCHS". The page excepts licensed third-party content;
  the ICD-10-CM code files carry no such notice.
* Mode: **shipped** (a starter subset of 16,582 codes with their whole validity history) and
  **fetch** (the full set, 99,085 codes). Releases: every October 1 from FY2016, plus the
  2021-01-01, 2022-04-01 and 2023-04-01 updates.
* Known gap `[VERIFY]`: the April 1, 2020 update (U07.1) has no file on the CDC or CMS pages
  that the builder can pin; U07.1 is reported invalid from 2020-04-01 to 2020-09-30.

## ICD-10-PCS (CMS)

* Source: https://www.cms.gov/medicare/coding-billing/icd-10-codes (`/files/zip/<year>-icd-10-pcs-order-file-...zip`).
* Licence: the CMS page states no copyright or reuse terms. The files are a work of the US
  federal government (17 U.S.C. 105). `[VERIFY]`: CMS publishes no statement this plugin could
  quote.
* Mode: **fetch** only. Releases FY2021 to FY2027 plus the April 2025 and April 2026 updates.
  Files CMS labels "updated" (FY2021 to FY2024) are treated as effective October 1.

## HCPCS Level II (CMS)

* Source: https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update
  (`/files/zip/<month>-<year>-alpha-numeric-hcpcs-file.zip`).
* Licence: the page states none. The same CMS site says "HCPCS Level I: Comprised of Current
  Procedural Terminology (CPT), a numeric coding system maintained by the American Medical
  Association (AMA)", and the zip's record layout says CPT-4 codes and descriptors are
  copyrighted by the AMA ("Any other use violates the AMA copyright"). The Level II file this
  plugin reads holds codes that begin with a letter only; the builder **refuses** any code
  record that begins with a digit (`CptRecordError`) instead of storing it. `[VERIFY]` as for
  ICD-10-PCS.
* Mode: **fetch** only. CPT itself is **BYO**.

## FDA NDC Directory

* Source: https://www.accessdata.fda.gov/cder/ndctext.zip (`product.txt`, `package.txt`).
* Licence: FDA Website Policies, https://www.fda.gov/about-fda/about-website/website-policies:
  "Unless otherwise noted, the contents of the FDA website (www.fda.gov) — both text and
  graphics — are not copyrighted. They are in the public domain and may be republished,
  reprinted and otherwise used freely by anyone without the need to obtain permission from
  FDA." openFDA terms, https://open.fda.gov/terms/: "public domain and made available with a
  Creative Commons CC0 1.0 Universal dedication."
* Mode: **fetch**. The file is 70 MB uncompressed (the compact build is 15.6 MB), changes
  daily, and has no publisher checksum: the manifest records the SHA-256 of the bytes used.

## RxNorm current prescribable content (NLM)

* Source: https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html, file
  `RxNorm_full_prescribe_09082026.zip`, marked "(no license required)", MD5
  `88bbe4cefabd8e71f58651c1c3188646` (pinned; reproduced on download).
* Licence: https://www.nlm.nih.gov/research/umls/rxnorm/docs/termsofservice.html: "The RxNorm
  terminology normalized names and codes (e.g. RXCUI) within the RxNorm dataset is created by
  the National Library of Medicine (NLM) and is in the public domain as it is created by the
  U.S. government. Public domain information may be freely distributed and copied within and
  outside the U.S., but it is requested that in any subsequent use, NLM be given appropriate
  acknowledgement." NLM requests that a product using RxNorm include: "This product uses
  publicly available data courtesy of the U.S. National Library of Medicine (NLM), National
  Institutes of Health, Department of Health and Human Services; NLM is not responsible for the
  product and does not endorse or recommend this or any other product." Those who redistribute
  the data agree "to maintain the most current version of all distributed data, or make known
  in a clear and conspicuous manner that the products/services/applications do not reflect the
  most current/accurate data available from NLM." The full RxNorm release needs a (free) UMLS
  licence and is **not** used.
* Mode: **fetch**, checksum-pinned. Not shipped, because a monthly release would go stale. The
  manifest carries the statement and the release date.

## CMS place-of-service codes

* Source: https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets (the
  table on the page; "database updated May 2, 2024").
* Licence: the page states none; a US federal government work. `[VERIFY]`.
* Mode: **fetch** only.

## CMS-HCC, ESRD and RxHCC mappings (CMS)

* Source: https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment
  ("2027 Initial ICD-10-CM Mappings", file of 2026-05-13; V21, V22, V24, V28, RxHCC V08).
* Licence: the page states none; a US federal government work. `[VERIFY]`.
* Mode: **fetch** only.

## CMS-HCC, ESRD and RxHCC hierarchies (CMS)

* Source: https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment/2027-model-software-icd-10-mappings,
  file https://www.cms.gov/files/zip/2027-initial-model-software.zip ("2027 Initial Model
  Software", packages of 2026-05-04: CMS-HCC V2826.115.T2 and V2226.79.O2, ESRD E2426.86.T2 and
  E2126.87.P2, RxHCC R0826.84.T2, .Y1 and .Y2), retrieved 2026-10-04. The hierarchy macros
  (`V28115H1`, `V22H79H1`, `V24H86H1`, `V20H87H1`, `R08X84H1`).
* Licence: neither the model software page nor the risk-adjustment page states one (read
  2026-10-04); a US federal government work. `[VERIFY]`.
* Mode: **fetch** only (or `byo` with the same zip, or a delimited file of the table's columns).

## CMS-HCC, ESRD and RxHCC coefficients (CMS)

* Source: the same zip as the hierarchies, retrieved 2026-10-04: the coefficient files of each
  package (`C2824T2N.csv`, `C2214O5P.csv`, `D2423T2M.csv`, `D2117P2R.csv`, `R0827T11.csv`,
  `R0827Y61.csv`, `R0827Y51.csv`), with the segment names read from each package's main macro and
  the payment year from its main program.
* Licence: as for the hierarchies. `[VERIFY]`.
* Mode: **fetch** only (or `byo`, as for the hierarchies). The plugin's tests carry short
  excerpts of these files and of the 2027 mappings CSV, byte for byte, under `tests/data/`.

## AHRQ CCSR for ICD-10-CM diagnoses

* Source: https://hcup-us.ahrq.gov/toolssoftware/ccsr/dxccsr.jsp (v2026.1, released 2025-11-19,
  valid through 2026-09-30; the v2027 release follows).
* Licence: the page states none for the tool and the reference file; the HCUP Data Use
  Agreement governs HCUP *databases*. AHRQ is a US federal agency. `[VERIFY]`.
* Mode: **fetch** only.

## Medicare Code Editor age and sex edits (CMS)

* Source: "Definitions of Medicare Code Edits", https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software
  : v44 (FY2027) for the age lists, v41.1 for the sex lists.
* Licence: the page states none; a US federal government work. `[VERIFY]`.
* Note: CMS "deactivated" the sex-conflict edit as of 2024-10-01 and stopped listing it, so the
  sex lists are those of v41.1 (effective 2024-04-01 to 2024-09-30); codes added after FY2024
  are not on them.
* Mode: **fetch** only.

## NUCC Health Care Provider Taxonomy

* Source: https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/csv-mainmenu-57
  (version 26.1, effective 2026-07-01).
* Licence: "For commercial use, including sales or licensing, a license must be obtained from
  this web site." "Copyright 2026 American Medical Association". Shipping it in a product is
  commercial redistribution.
* Mode: **BYO**. The loader reads the user's own `nucc_taxonomy_<version>.csv`.

## CARC claim adjustment reason codes (X12)

* Source: https://x12.org/codes/claim-adjustment-reason-codes
* Licence: "All X12 work products are copyrighted. Any use of any X12 work product must be
  compliant with US Copyright laws and X12 Intellectual Property policies." No redistribution
  grant is stated.
* Mode: **BYO**. The loader reads the user's own code and description file.

## RARC remittance advice remark codes (X12)

* Source: https://x12.org/codes/remittance-advice-remark-codes. The same statement as for CARC
  applies (read on https://x12.org/codes/claim-adjustment-reason-codes).
* Mode: **BYO**.

## WHO ICD-10

* Source: https://icd.who.int/browse10/2019/en. Copyright of the World Health Organization; the
  browser page states no redistribution licence. (WHO states that ICD-11 is distributed under
  CC BY-ND 3.0 IGO, which forbids derivatives; ICD-10 is not stated.) `[VERIFY]`.
* Mode: **BYO** (WHO ClaML).

## ICD-10-GM (BfArM)

* Source: https://www.bfarm.de/EN/Code-systems/Classifications/ICD/ICD-10-GM/_node.html.
* Licence: "BfArM publishes the ICD-10-GM on behalf of the Federal Ministry of Health; it is in
  the public domain." But: "With the download of files a contract of use between you and the
  BfArM comes into being." The contract is made by the person who downloads, so this plugin
  neither downloads nor redistributes the files.
* Mode: **BYO** (ClaML).

## ICD-10-AM / ACHI / ACS (IHACPA)

* Source: https://www.ihpa.gov.au/. The host was not reachable from the build environment (the
  proxy answered 502 on 2026-10-02), so no licence text was read. The classification is
  licensed by IHACPA. `[VERIFY]`.
* Mode: **BYO**. The loader is tested on a hand-made fixture only.

## CPT (American Medical Association)

Licensed by the AMA. **BYO** only: `shape healthcare-codes byo cpt FILE`. Never shipped, never
downloaded, never read from the HCPCS file.

## SNOMED CT (SNOMED International)

Licensed under the SNOMED International affiliate licence (free in member territories such as
the United States, but not redistributable). **BYO** only.

## UB-04 revenue codes (NUBC)

Licensed by the National Uniform Billing Committee. **BYO** only.

## UB-04 type of bill (NUBC)

Licensed by the National Uniform Billing Committee. **BYO** only.
