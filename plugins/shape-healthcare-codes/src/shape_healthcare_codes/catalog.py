"""The asset catalog: one :class:`~shape_healthcare_codes.provenance.Asset` per code set, with
the licence statement as read from the source's own page (see ``THIRD_PARTY_NOTICES.md``).
"""

from __future__ import annotations

from shape_healthcare_codes.provenance import Asset, register

_R = (
    "the page states no copyright or reuse terms; a work of the US federal government "
    "(17 U.S.C. 105)"
)

register(
    Asset(
        "icd10cm",
        "ICD-10-CM (CDC/NCHS)",
        "shipped",
        "https://ftp.cdc.gov/pub/Health_Statistics/NCHS/Publications/ICD10CM/",
        "FY2016 to FY2027 (effective 2026-10-01), plus the 2021-01, 2022-04 and 2023-04 updates",
        "public domain (attribution: Source: CDC/NCHS)",
        "https://www.cdc.gov/other/agencymaterials.html",
        "Most of the information on the CDC and ATSDR websites is not subject to copyright, is in "
        "the public domain, and may be freely used or reproduced without obtaining copyright "
        "permission.",
        notes="A starter subset is shipped in the wheel; the full set is built by `fetch`.",
    )
)
register(
    Asset(
        "icd10pcs",
        "ICD-10-PCS (CMS)",
        "fetch",
        "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "FY2021 to FY2027, plus the April 2025 and April 2026 updates",
        "US government work; " + _R,
        "https://www.cms.gov/medicare/coding-billing/icd-10-codes",
        "(no statement on the page)",
        verify=True,
    )
)
register(
    Asset(
        "hcpcs2",
        "HCPCS Level II (CMS)",
        "fetch",
        "https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update",
        "October 2026 alpha-numeric file (2026-09-23)",
        "US government work; " + _R + "; Level II only, CPT is never read",
        "https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system",
        "HCPCS Level I: Comprised of Current Procedural Terminology (CPT), a numeric coding "
        "system maintained by the American Medical Association (AMA).",
        verify=True,
    )
)
register(
    Asset(
        "ndc",
        "FDA NDC Directory",
        "fetch",
        "https://www.accessdata.fda.gov/cder/ndctext.zip",
        "daily file; the build records its date and SHA-256",
        "public domain; CC0 1.0 (openFDA terms)",
        "https://www.fda.gov/about-fda/about-website/website-policies",
        "Unless otherwise noted, the contents of the FDA website (www.fda.gov) are not "
        "copyrighted. They are in the public domain and may be republished, reprinted and "
        "otherwise used freely by anyone without the need to obtain permission from FDA.",
    )
)
register(
    Asset(
        "rxnorm",
        "RxNorm current prescribable content (NLM)",
        "fetch",
        "https://www.nlm.nih.gov/research/umls/rxnorm/docs/rxnormfiles.html",
        "RxNorm_full_prescribe_09082026.zip (MD5 88bbe4cefabd8e71f58651c1c3188646)",
        "public domain (NLM); no UMLS licence for the prescribable subset; NLM asks for "
        "acknowledgement and a currency disclosure",
        "https://www.nlm.nih.gov/research/umls/rxnorm/docs/termsofservice.html",
        "The RxNorm terminology normalized names and codes within the RxNorm dataset is created "
        "by the National Library of Medicine (NLM) and is in the public domain as it is created "
        "by the U.S. government.",
    )
)
register(
    Asset(
        "pos",
        "CMS place-of-service codes",
        "fetch",
        "https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets",
        "database updated 2024-05-02",
        "US government work; " + _R,
        "https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets",
        "(no statement on the page)",
        verify=True,
    )
)
register(
    Asset(
        "hcc",
        "CMS-HCC, ESRD and RxHCC mappings (CMS)",
        "fetch",
        "https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment",
        "2027 Initial ICD-10-CM Mappings",
        "US government work; " + _R,
        "https://www.cms.gov/medicare/payment/medicare-advantage-rates-statistics/risk-adjustment",
        "(no statement on the page)",
        verify=True,
    )
)
register(
    Asset(
        "ccsr",
        "AHRQ CCSR for ICD-10-CM diagnoses",
        "fetch",
        "https://hcup-us.ahrq.gov/toolssoftware/ccsr/dxccsr.jsp",
        "v2026.1",
        "no terms stated for the tool; AHRQ is a US federal agency",
        "https://hcup-us.ahrq.gov/toolssoftware/ccsr/dxccsr.jsp",
        "(no statement on the page; the HCUP Data Use Agreement covers HCUP databases)",
        verify=True,
    )
)
register(
    Asset(
        "mce_edits",
        "Medicare Code Editor age and sex edits (CMS)",
        "fetch",
        "https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/"
        "ms-drg-classifications-and-software",
        "age: v44 (FY2027); sex: v41.1 (last to publish them)",
        "US government work; " + _R,
        "https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/"
        "ms-drg-classifications-and-software",
        "(no statement on the page)",
        verify=True,
        notes="CMS deactivated the sex-conflict edit on 2024-10-01.",
    )
)
register(
    Asset(
        "nucc_taxonomy",
        "NUCC Health Care Provider Taxonomy",
        "byo",
        "https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/"
        "csv-mainmenu-57",
        "version 26.1 (effective 2026-07-01)",
        "copyright American Medical Association; commercial use needs a licence",
        "https://www.nucc.org/index.php/code-sets-mainmenu-41/provider-taxonomy-mainmenu-40/"
        "csv-mainmenu-57",
        "For commercial use, including sales or licensing, a license must be obtained from this "
        "web site. Copyright 2026 American Medical Association",
    )
)
register(
    Asset(
        "carc",
        "CARC claim adjustment reason codes (X12)",
        "byo",
        "https://x12.org/codes/claim-adjustment-reason-codes",
        "current",
        "X12 copyright; no redistribution grant",
        "https://x12.org/codes/claim-adjustment-reason-codes",
        "All X12 work products are copyrighted. Any use of any X12 work product must be "
        "compliant with US Copyright laws and X12 Intellectual Property policies.",
    )
)
register(
    Asset(
        "rarc",
        "RARC remittance advice remark codes (X12)",
        "byo",
        "https://x12.org/codes/remittance-advice-remark-codes",
        "current",
        "X12 copyright; no redistribution grant",
        "https://x12.org/codes/claim-adjustment-reason-codes",
        "All X12 work products are copyrighted. Any use of any X12 work product must be "
        "compliant with US Copyright laws and X12 Intellectual Property policies.",
    )
)
register(
    Asset(
        "icd10_who",
        "WHO ICD-10",
        "byo",
        "https://icd.who.int/browse10/2019/en",
        "2019",
        "WHO copyright; no licence stated on the browser page",
        "https://icd.who.int/browse10/2019/en",
        "(no licence statement on the page)",
        verify=True,
    )
)
register(
    Asset(
        "icd10gm",
        "ICD-10-GM (BfArM)",
        "byo",
        "https://www.bfarm.de/EN/Code-systems/Classifications/ICD/ICD-10-GM/_node.html",
        "2027",
        "public domain per BfArM, but a download is a contract of use with BfArM",
        "https://www.bfarm.de/EN/Code-systems/Classifications/ICD/ICD-10-GM/_node.html",
        "BfArM publishes the ICD-10-GM on behalf of the Federal Ministry of Health; it is in the "
        "public domain. With the download of files a contract of use between you and the BfArM "
        "comes into being.",
    )
)
register(
    Asset(
        "icd10am",
        "ICD-10-AM / ACHI / ACS (IHACPA)",
        "byo",
        "https://www.ihpa.gov.au/",
        "unknown (host not reachable from the build environment)",
        "licensed by IHACPA; not read",
        "https://www.ihpa.gov.au/",
        "(host blocked: 502 from the proxy on 2026-10-02)",
        verify=True,
    )
)
for _id, _title in (
    ("cpt", "CPT (American Medical Association)"),
    ("snomed", "SNOMED CT (SNOMED International)"),
    ("revenue_codes", "UB-04 revenue codes (NUBC)"),
    ("type_of_bill", "UB-04 type of bill (NUBC)"),
):
    register(
        Asset(
            _id,
            _title,
            "byo",
            "(licensed; obtain from the owner)",
            "user supplied",
            "licensed; no redistribution",
            "(licence from the owner)",
            "licensed content: bring your own file",
        )
    )
