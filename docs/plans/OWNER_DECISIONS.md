# Owner decisions

Recorded by the Owner on **2026-10-11**, for `docs/owner-notes` at `514f6f9b4758735565493f668457a67a3375609f`.

The decisions below are verbatim Owner instructions. Note locations are the original inventory locations in `docs/plans/OWNER_NOTES_TRIAGE.md`, not the post-edit line numbers. Each entry records the evidence column of the corresponding row in `docs/plans/OWNER_ANSWERS_DRAFT.md` at that commit. That draft remains a historical proposal; the Owner decisions supply the missing sign-off and authorship testimony without implying independent upstream research or counsel approval.

Entries marked KEEP OPEN record future work or a deferred decision only; their public owner notes remain unchanged. This record does not modify the release gate.

## governance

Date: **2026-10-11**. Note: `GOVERNANCE.md:8`. Decision: **RESOLVE**.

> Jonathan Stewart is the sole maintainer, with final review and merge authority. No review or merge authority is delegated.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:11` (note `GOVERNANCE.md:8`).

Evidence relied on: `LICENSE:1–13`; `GOVERNANCE.md:5–15`; `CONTRIBUTING.md:24–38` (the copyright identifies Jonathan, while governance assigns authority to the repository owner; neither proves a named roster).

## security

Date: **2026-10-11**. Note: `SECURITY.md:8`. Decision: **RESOLVE**.

> Only the latest release is supported. Report vulnerabilities privately as SECURITY.md describes. Responses are best-effort: no response-time commitment and no backports.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:12` (note `SECURITY.md:8`).

Evidence relied on: `SECURITY.md:5–12`; `GOVERNANCE.md:5–7`; `pyproject.toml:5–12`; `LICENSE:15–21` (no supported-patch matrix or response SLA is recorded).

## domain-education

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:246`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:14` (note `THIRD_PARTY_NOTICES.md:246`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/education.py:15–17`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01a.md:26–38`; `THIRD_PARTY_NOTICES.md:246–246`; `plugins/shape-domains/LICENSE:1–13`; import commit `66ae00f034063f0b51d043d6c8422d7b0ca7224c` (domain module/data introduction).

## domain-financial

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:247`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:15` (note `THIRD_PARTY_NOTICES.md:247`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/financial.py:16–19`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01a.md:26–38`; `THIRD_PARTY_NOTICES.md:247–247`; `plugins/shape-domains/LICENSE:1–13`; import commit `66ae00f034063f0b51d043d6c8422d7b0ca7224c` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-hr

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:249`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:17` (note `THIRD_PARTY_NOTICES.md:249`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/hr.py:15–19`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01b.md:25–37`; `THIRD_PARTY_NOTICES.md:249–249`; `plugins/shape-domains/LICENSE:1–13`; import commit `79fcb92791dd50045a4fc86f1012d2fa8080cb06` (domain module/data introduction).

## domain-insurance

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:250`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:18` (note `THIRD_PARTY_NOTICES.md:250`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/insurance.py:15–18`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01b.md:25–37`; `THIRD_PARTY_NOTICES.md:250–250`; `plugins/shape-domains/LICENSE:1–13`; import commit `79fcb92791dd50045a4fc86f1012d2fa8080cb06` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-iot

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:251`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:19` (note `THIRD_PARTY_NOTICES.md:251`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/iot.py:15–18`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01c.md:16–27`; `THIRD_PARTY_NOTICES.md:251–251`; `plugins/shape-domains/LICENSE:1–13`; import commit `9aa6f14640143732000335ed4f72fc28ee850d44` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-manufacturing

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:252`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:20` (note `THIRD_PARTY_NOTICES.md:252`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/manufacturing.py:14–18`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01c.md:16–27`; `THIRD_PARTY_NOTICES.md:252–252`; `plugins/shape-domains/LICENSE:1–13`; import commit `9aa6f14640143732000335ed4f72fc28ee850d44` (domain module/data introduction).

## domain-marketing

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:253`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:21` (note `THIRD_PARTY_NOTICES.md:253`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/marketing.py:14–16`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01c.md:16–27`; `THIRD_PARTY_NOTICES.md:253–253`; `plugins/shape-domains/LICENSE:1–13`; import commit `9aa6f14640143732000335ed4f72fc28ee850d44` (domain module/data introduction).

## domain-real_estate

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:254`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:22` (note `THIRD_PARTY_NOTICES.md:254`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/real_estate.py:15–20`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01d.md:24–36`; `THIRD_PARTY_NOTICES.md:254–254`; `plugins/shape-domains/LICENSE:1–13`; import commit `efae495932e456949a5b864135e3d6cfb6c28f8d` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-supply_chain

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:256`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:23` (note `THIRD_PARTY_NOTICES.md:256`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/supply_chain.py:16–19`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01d.md:24–36`; `THIRD_PARTY_NOTICES.md:256–256`; `plugins/shape-domains/LICENSE:1–13`; import commit `efae495932e456949a5b864135e3d6cfb6c28f8d` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-telecom

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:257`. Decision: **RESOLVE**.

> The reference lists were authored by the Owner for the earlier SQLLocks generator, written by hand or generated with AI assistance; they were not copied from a specific external website or dataset. Keep every existing attribution exactly (including GeoNames ZIP data under CC-BY-4.0 and the recorded MIT notices).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:24` (note `THIRD_PARTY_NOTICES.md:257`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/telecom.py:15–20`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01d.md:24–36`; `THIRD_PARTY_NOTICES.md:257–257`; `plugins/shape-domains/LICENSE:1–13`; import commit `efae495932e456949a5b864135e3d6cfb6c28f8d` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## domain-pulse

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:258`. Decision: **RESOLVE**.

> Same as 3–12: the name pools were authored by the Owner (by hand or with AI assistance), not copied from an external source; values are generated by the built-in seeded providers. Keep existing MIT notices.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:25` (note `THIRD_PARTY_NOTICES.md:258`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/pulse.py:1–15`; `plugins/shape-domains/src/shape_domains/_packaged.py:41–44,51–64`; `plugins/shape-domains/src/shape_domains/data/pulse/schema.json:35–68,167–200`; `plugins/shape-domains/src/shape_domains/data/pulse/schema_star.json:35–68,167–200`; `src/shape/builtins/strategies/providers.py:1–9,92–105,113–122,160–200,322–328,578–587`; `benchmarks/vs_refengine/strategy_1to1/export_pools.py:30–48,62–76`; `THIRD_PARTY_NOTICES.md:258`; `LICENSE:1–13`.

## licensing

Date: **2026-10-11**. Note: `docs/LICENSING.md:3`. Decision: **RESOLVE**.

> Approved: publish the narrow statement only — Shape code is MIT; inbound contributions are MIT with DCO sign-off; keep "Not legal advice"; keep the reference-data notice obligations. No blanket clearance. Counsel review is deferred by the Owner; say nothing that implies counsel approved it.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:28` (note `docs/LICENSING.md:3`).

Evidence relied on: `LICENSE:1–21`; `CONTRIBUTING.md:24–27`; `docs/LICENSING.md:3,9–20,22–34`; `THIRD_PARTY_NOTICES.md:7–17,237–258`; `plugins/shape-domains/LICENSE:1–13` (ownership language is an existing project statement, not a legal opinion).

## threat-model

Date: **2026-10-11**. Note: `docs/THREAT_MODEL.md:6`. Decision: **RESOLVE**.

> Approved: describe the controls the code implements and the residual risks. No assurance about any particular deployment.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:29` (note `docs/THREAT_MODEL.md:6`).

Evidence relied on: `security/THREAT_MODEL.md:1–8`; `docs/THREAT_MODEL.md:8–30,164–186`; `src/shape/plugins/host.py:332–350`; `docs/WHAT_LEAVES.md:46–54`.

## outbound-paths

Date: **2026-10-11**. Note: `docs/WHAT_LEAVES.md:54`. Decision: **RESOLVE**.

> Approved: describe the configured outbound paths and process identity as implemented. No deployment-specific endpoints, identities or egress rules are approved; say so.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:30` (note `docs/WHAT_LEAVES.md:54`).

Evidence relied on: `docs/WHAT_LEAVES.md:7–8,24–54`; `src/shape/builtins/sources/azure.py:1–17,71–90,94–112`; `src/shape/plugins/host.py:332–350`; `docs/THREAT_MODEL.md:146–152,182–186`; `.github/workflows/docs.yml:38–44,57–79`; `docs/CNAME:1–1` (the public docs hostname and publishing identity do not establish user-service deployment approval).

## domain-capital-markets

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:245`. Decision: **KEEP OPEN**.

> GICS sectors, codes and industry names (MSCI/S&P taxonomy) in plugins/shape-domains/.../capital_markets/reference/gics_sectors.arrow will be replaced with a generic synthetic sector list in 0.9.2. Lists other than GICS: authored by the Owner as in 3–12.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:13` (note `THIRD_PARTY_NOTICES.md:245`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/capital_markets.py:15–20`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01a.md:26–38`; `THIRD_PARTY_NOTICES.md:245–245`; `plugins/shape-domains/LICENSE:1–13`; import commit `66ae00f034063f0b51d043d6c8422d7b0ca7224c` (domain module/data introduction).

## domain-healthcare

Date: **2026-10-11**. Note: `THIRD_PARTY_NOTICES.md:248`. Decision: **KEEP OPEN**.

> the 49 CPT codes (AMA code set) in plugins/shape-domains/.../healthcare/reference/cpt_codes.arrow will be replaced with clearly synthetic procedure codes in 0.9.2. Do not make any CPT or ICD licensing claim. Other healthcare lists: authored by the Owner as in 3–12.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:16` (note `THIRD_PARTY_NOTICES.md:248`).

Evidence relied on: `plugins/shape-domains/src/shape_domains/healthcare.py:16–19`; `plugins/shape-domains/src/shape_domains/_packaged.py:21–33,51–64`; `benchmarks/vs_refengine/domain_1to1/export_domains.py:48–75,100–105,125–134`; `docs/plans/lane_status/P6-01b.md:25–37`; `THIRD_PARTY_NOTICES.md:248–248`; `plugins/shape-domains/LICENSE:1–13`; import commit `79fcb92791dd50045a4fc86f1012d2fa8080cb06` (domain module/data introduction). `THIRD_PARTY_NOTICES.md:7–17,255–255`; `plugins/shape-domains/THIRD_PARTY_NOTICES.md:7–17`.

## dataset-validator

Date: **2026-10-11**. Note: `docs/KNOWN_LIMITATIONS.md:16`. Decision: **KEEP OPEN**.

> stays until a committed dataset safe-validator fix passes (0.9.2).

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:27` (note `docs/KNOWN_LIMITATIONS.md:16`).

Evidence relied on: `docs/KNOWN_LIMITATIONS.md:14–16,20–23`; `src/shape/cli/profiles.py:25–31`; `src/shape/privacy/cli.py:101–120`; `src/shape/privacy/safe_validator.py:1–23,229–299`; `tests/privacy/test_safe_validator.py:109–137`; `docs/WHAT_LEAVES.md:7–8,14–22`.

## required-status-check

Date: **2026-10-11**. Note: `docs/GITHUB_ACTION.md:84`. Decision: **KEEP OPEN**.

> required status check decided after the hosted GitHub Action live run.

Evidence row: `docs/plans/OWNER_ANSWERS_DRAFT.md:26` (note `docs/GITHUB_ACTION.md:84`).

Evidence relied on: `docs/GITHUB_ACTION.md:32–52,76–84`; `action.yml:91–100`; `.github/workflows/ci.yml:1–18,55–77`; `.github/workflows/docs.yml:10–33` (workflow/job declarations are evidence of configuration, not live branch-protection state).
