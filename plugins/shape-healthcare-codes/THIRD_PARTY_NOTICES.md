# Third-party notices: healthcare code sets

One entry per asset. Every licence statement was read on the source's own page on the date
shown ("checked"), and quoted where the page states one. **Shipped** means inside the wheel;
**fetch** means `shape healthcare-codes fetch` downloads it from the official source on the
user's machine; **BYO** means the user supplies the file and nothing is downloaded or shipped by
this plugin. A statement the page does not make is not assumed: the asset is then fetch-only or
BYO, and marked `[VERIFY]` when a person should confirm.

Sizes and checksums are recorded in `docs/plans/lane_status/HC-codes.md` and in each built
asset's manifest.

## ICD-10-CM (CDC / NCHS)

* Source: https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/ (one directory
  per fiscal year; FY2027 is the release in force from 2026-10-01).
* Licence: CDC, "Use of Agency Materials", https://www.cdc.gov/other/agencymaterials.html
  (checked 2026-10-02): "Most of the information on the CDC and ATSDR websites is not subject to
  copyright, is in the public domain, and may be freely used or reproduced without obtaining
  copyright permission." Attribution is required: "Source: CDC/NCHS". The page excepts licensed
  third-party content; the ICD-10-CM code files carry no such notice.
* Mode: **fetch**, plus a **shipped** starter subset (a few thousand codes) for tests and offline
  work. The full set is built by the user.

## ICD-10-PCS (CMS)

* Source: https://www.cms.gov/medicare/coding-billing/icd-10-codes (files named
  `<year>-icd-10-pcs-order-file-long-abbreviated-titles.zip`).
* Licence: the CMS page states no copyright or reuse terms (checked 2026-10-02). The files are
  a work of the US federal government (17 U.S.C. 105). `[VERIFY]` CMS publishes no statement
  this plugin could quote.
* Mode: **fetch** only. Not shipped.

## HCPCS Level II (CMS)

* Source: https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update
  (`/files/zip/<month>-<year>-alpha-numeric-hcpcs-file.zip`).
* Licence: the page states none (checked 2026-10-02). The record layout in the zip says CPT-4
  codes and descriptors are copyrighted by the American Medical Association and "Any other use
  violates the AMA copyright". The Level II file this plugin reads holds only codes beginning
  with a letter (verified at build: the builder refuses any record whose code begins with a
  digit), so no CPT code or descriptor is read. `[VERIFY]` as for ICD-10-PCS.
* Mode: **fetch** only. Not shipped. CPT is **BYO**.

## FDA NDC Directory

* Source: https://www.accessdata.fda.gov/cder/ndctext.zip (`product.txt`, `package.txt`).
* Licence: FDA Website Policies, https://www.fda.gov/about-fda/about-website/website-policies
  (checked 2026-10-02): "Unless otherwise noted, the contents of the FDA website ... are not
  copyrighted. They are in the public domain and may be republished, reprinted and otherwise
  used freely by anyone without the need to obtain permission from FDA." openFDA terms,
  https://open.fda.gov/terms/: "public domain and made available with a Creative Commons CC0
  1.0 Universal dedication."
* Mode: **fetch** (the source is 70 MB uncompressed and changes daily; the build is dated, not
  pinned by checksum, because the FDA publishes no checksum). Not shipped.

## RxNorm, current prescribable content (NLM)

* Source: https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html, file
  `RxNorm_full_prescribe_<MMDDYYYY>.zip`, marked "(no license required)" with a published MD5.
* Licence: RxNorm terms of service,
  https://www.nlm.nih.gov/research/umls/rxnorm/docs/termsofservice.html (checked 2026-10-02):
  names and codes created by NLM are public domain, with acknowledgement requested; products
  must state "This product uses publicly available data courtesy of the U.S. National Library
  of Medicine (NLM), National Institutes of Health, Department of Health and Human Services;
  NLM is not responsible for the product and does not endorse or recommend this or any other
  product."; whoever redistributes must keep the data current or disclose that it is not the
  most current. The full RxNorm release needs a UMLS licence and is **not** used.
* Mode: **fetch**, checksum-pinned. Not shipped (a monthly release; redistribution would go
  stale).

## NUCC Health Care Provider Taxonomy

* Source: https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/csv-mainmenu-57
* Licence (checked 2026-10-02): "Copyright 2026 American Medical Association. ... For commercial
  use, including sales or licensing, a license must be obtained from this web site." Shipping it
  in a product is commercial redistribution.
* Mode: **BYO**. The loader reads the user's own `nucc_taxonomy_<version>.csv`.

## CMS place-of-service codes

* Source: https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets
* Licence: the page states none (checked 2026-10-02); a US federal government work.
  `[VERIFY]`.
* Mode: **fetch** only (a PDF table the builder parses).

## CMS-HCC and RxHCC mappings (CMS)

* Source: https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment
  ("<year> Model Software/ICD-10 Mappings").
* Licence: the page states none (checked 2026-10-02); a US federal government work. `[VERIFY]`.
* Mode: **fetch** only.

## AHRQ CCSR for ICD-10-CM diagnoses

* Source: https://hcup-us.ahrq.gov/toolssoftware/ccsr/dxccsr.jsp (v2026.1 is valid through
  2026-09-30; the next release follows each October).
* Licence: the page states none for the tool and reference file (checked 2026-10-02); the HCUP
  Data Use Agreement governs HCUP *databases*, not this tool. `[VERIFY]`.
* Mode: **fetch** only.

## CARC and RARC (X12)

* Source: https://x12.org/codes/claim-adjustment-reason-codes
* Licence (checked 2026-10-02): "All X12 work products are copyrighted. Any use of any X12 work
  product must be compliant with US Copyright laws and X12 Intellectual Property policies."
  No redistribution grant is stated.
* Mode: **BYO**. The loader reads the user's own two-column code and description file.

## International: WHO ICD-10, ICD-10-GM, ICD-10-AM

* WHO ICD-10 (https://icd.who.int/browse10/2019/en): copyright of the World Health
  Organization; the browser page states no redistribution licence (checked 2026-10-02).
  `[VERIFY]`. Mode: **BYO** (WHO ClaML).
* ICD-10-GM (BfArM, https://www.bfarm.de/EN/Code-systems/Classifications/ICD/ICD-10-GM/_node.html,
  checked 2026-10-02): "BfArM publishes the ICD-10-GM on behalf of the Federal Ministry of
  Health; it is in the public domain." But "With the download of files a contract of use
  between you and the BfArM comes into being." The contract is made by the person who
  downloads, so this plugin does not download or redistribute it. Mode: **BYO** (ClaML).
* ICD-10-AM / ACHI / ACS (IHACPA, https://www.ihpa.gov.au/): licensed by IHACPA; the host was
  not reachable from the build environment (proxy refused, 2026-10-02), so no licence text was
  read. `[VERIFY]`. Mode: **BYO**.

## Licensed sets with no builder: CPT, SNOMED CT, UB-04 revenue codes and type of bill

CPT (American Medical Association), SNOMED CT (SNOMED International affiliate licence) and the
NUBC UB-04 code sets are licensed. They are **BYO** only: the generic loader
(`shape healthcare-codes byo`) reads a file the user licensed.
