# Questions

## #4 W9-03 — unbounded indexed strings need an Owner decision

Status: RESOLVED by Owner Option 1 on 2026-10-20; lane unparked. Historical question and options follow.

Wanted2 requires string lengths only from declared max_length/schema width, otherwise unbounded. SQL Server cannot use VARCHAR(MAX)/NVARCHAR(MAX) in a PRIMARY KEY. Existing live Fabric code instead guesses 450 for undeclared indexed strings; retaining that fallback conflicts with Wanted2 and script/live equivalence.

Exact corpus cases: derived/customer.customer_id and foreign_key/e.eid have string primary keys without declared widths. Across tsql and tsql-fabric-warehouse this is four additional refused outputs if validated safely. Owner comments authorize 24decimal refusals and listedDDL/string-width updates; they do not authorize these four new string-key refusals. Existing Fabric assertion expects guessed NVARCHAR(450).

Question: should missing declared string widths on indexed SQL Server/Fabric columns fail before a connection, requiring max_length, and may the four original corpus inputs now assert column-named refusals?

Options:
- Approve named preconnection refusal for undeclared indexed strings, document max_length as required for these keys, and authorize the four additional exact corpus refusals. Recommended: preserves requested type fidelity and never guesses a width.
- Amend Wanted2 to permit a documented bounded 450 indexed-string exception, with explicit enforcement of that limit. This changes the requested unbounded default for keys.
- Remove indexed-string cases from W9-03 scope. Not recommended: leaves script/live type mismatch.

No gate, tolerance, existing input, or unapproved corpus refusal was changed to resolve this conflict. Work is preserved on lane/W9-03; no merge is authorized until decided and all checks pass.

## #5 W9-04 — DuckDB foreign-key phase needs an Owner amendment

Status: RESOLVED by Owner Option 1 on 2026-10-20; lane unparked. Historical evidence: installed DuckDB 1.5.6 and independent reviewer both reproduce NotImplementedException for ALTER TABLE child ADD CONSTRAINT FOREIGN KEY after parent and child exist. Parent-first CREATE TABLE child with an inline foreign key is supported and enforces on load.

Question: may DuckDB create foreign keys inline when creating each child after its parents, as a documented dialect exception to Wanted3's requirement that foreign keys be added after both tables exist?

Options:
- Approve the DuckDB-only parent-first inline exception; keep prepare-all-before-load, enforced constraints, named failures and existing transaction rules. Recommended: uses supported SQL and preserves intended enforcement.
- Require the literal phase on every dialect and remove DuckDB from this issue's supported constraint targets. Changes scope and leaves DuckDB unchanged.
- Defer the issue until DuckDB supports the required ALTER operation. No speculative drop/recreate workaround is recommended.

Concrete partial work: safe constraint derivation/rendering and bounded schema-preparation orchestration are committed on lane/W9-04; 77 focused tests pass. Writer integration and full acceptance remain incomplete. No constraint gate, assertion, or tolerance was weakened.

## #8 W9-07 — blocked on the earlier W9-04 decision

Status: RESOLVED by Owner on 2026-10-20; lane unparked, final integration waits for #5 to merge. Historical dependency: the preceding #5 Owner decision. Wanted2 explicitly reuses W9-04's constraint derivation, and the run requires an earlier issue to merge before a dependent one. W9-04 cannot currently merge under its literal DuckDB phase requirement.

Question: approve the recommended DuckDB exception for #5 so its safe constraint helper can merge, then merge the already-stacked helper into main before #8?

Recommendation: resolve #5 and merge it first, then finish targeted local acceptance and require full exact-head CI. The actual #5 helper is now stacked and its AST integration tests pass; no helper was duplicated and arbitrary check SQL remains refused. Unsupported native feature combinations use the already-authorized capability-refusal rules in Wanted8/#7Wanted6, requiring no additional Owner amendment. Independently implemented Delta features and their review fixes remain preserved on lane/W9-07; no ready-to-merge claim.

## Owner resolutions — 2026-10-20 resume

- #4 Option1 approved: named preconnection refusal for undeclared-width indexed strings, required key max_length and four extra exact corpus refusals.
- #5 Option1 approved: documented DuckDB-only parent-first inline FK exception, preserving preparation, enforcement, named errors and transaction rules.
- #8 finish after #5 main merge and integrate its actual constraint helper.

These questions are resolved; all three lanes resumed.

## RESOLVED — #4 frozen Bridge DDL contract (Owner 2026-10-26)

Status: RESOLVED by the Owner on 2026-10-26 through version-specific rendering. #4 is unparked and included in the final integration batch. The original question below is historical. Issue evidence/question: https://github.com/sqllocks/shape/issues/4#issuecomment-6302585629. Preserved code63ab15c is independently code-approved, with87follow-up tests green; frozen gate remains red.

`tests/bridge/test_compat_1_1.py` explicitly replays frozen1.1 responses with every field equal except api_version. Required canonical string mapping changes only five historical result.ddl declarations in docs/bridge/vectors/1.1/design.json: star.month_name and day_of_week_name NVARCHAR(255)→NVARCHAR(MAX); postgres-3nf.city.city, city_city.city and city_city.country VARCHAR(255)→TEXT. All other response fields are unchanged. Frozen vectors/fixtures remain untouched. Current nonfrozen vectors carry the actual corrected DDL. No existing version-aware DDL compatibility path exists.

Options:
- Explicitly amend the frozen-contract policy to authorize exactly these five historical DDL corrections, retaining one canonical map and exact assertions. Recommended, but it is an Owner contract-policy amendment, not a routine fixture refresh.
- Preserve historical1.1 DDL exactly and authorize a scoped compatibility/version design that can coexist with Wanted2 and the no-legacy rule. That requires scope/policy approval; no speculative fallback or guessed key width is implemented.
- Keep #4 parked until the contract policy is resolved.

Other six issues continue. No gate, assertion, tolerance, frozen artifact or Owner-approved refusal count changed to hide this conflict.

## Owner ruling — Bridge 1.3, 2026-10-26

Do not amend frozen Bridge outputs. Requests for 1.1 and 1.2 retain their historical DDL, including dim_date.month_name/day_of_week_name NVARCHAR(255), and dw.city.city/city_city.city/city_city.country VARCHAR(255). Frozen fields, exact assertions, fixtures and baselines remain unchanged.

CLI and Python API use corrected unbounded types; new Bridge minor1.3 does too. Add1.3 under existing versioning and negotiation rules. Preserve undeclared-width indexed-string named preconnection refusal on every version. New1.3/CLI/API tests cover all five columns, negative and boundary cases. Stable-API changes are additive only. Add docs/BRIDGE.md1.3 explanation and Unreleased changelog entry.

The Owner confirmed this resolves the last policy blocker and directed inclusion of #4 with #5/#7/#8 in the ONE final integration PR. No unresolved Owner policy questions remain. Full exact-head CI and independent review remain required.
