# Questions

## #4 W9-03 — unbounded indexed strings need an Owner decision

Status: parked pending Owner decision; independent reviewer confirmed the conflict.

Wanted2 requires string lengths only from declared max_length/schema width, otherwise unbounded. SQL Server cannot use VARCHAR(MAX)/NVARCHAR(MAX) in a PRIMARY KEY. Existing live Fabric code instead guesses 450 for undeclared indexed strings; retaining that fallback conflicts with Wanted2 and script/live equivalence.

Exact corpus cases: derived/customer.customer_id and foreign_key/e.eid have string primary keys without declared widths. Across tsql and tsql-fabric-warehouse this is four additional refused outputs if validated safely. Owner comments authorize 24decimal refusals and listedDDL/string-width updates; they do not authorize these four new string-key refusals. Existing Fabric assertion expects guessed NVARCHAR(450).

Question: should missing declared string widths on indexed SQL Server/Fabric columns fail before a connection, requiring max_length, and may the four original corpus inputs now assert column-named refusals?

Options:
- Approve named preconnection refusal for undeclared indexed strings, document max_length as required for these keys, and authorize the four additional exact corpus refusals. Recommended: preserves requested type fidelity and never guesses a width.
- Amend Wanted2 to permit a documented bounded 450 indexed-string exception, with explicit enforcement of that limit. This changes the requested unbounded default for keys.
- Remove indexed-string cases from W9-03 scope. Not recommended: leaves script/live type mismatch.

No gate, tolerance, existing input, or unapproved corpus refusal was changed to resolve this conflict. Work is preserved on lane/W9-03; no merge is authorized until decided and all checks pass.

## #5 W9-04 — DuckDB foreign-key phase needs an Owner amendment

Status: parked; installed DuckDB 1.5.6 and independent reviewer both reproduce NotImplementedException for ALTER TABLE child ADD CONSTRAINT FOREIGN KEY after parent and child exist. Parent-first CREATE TABLE child with an inline foreign key is supported and enforces on load.

Question: may DuckDB create foreign keys inline when creating each child after its parents, as a documented dialect exception to Wanted3's requirement that foreign keys be added after both tables exist?

Options:
- Approve the DuckDB-only parent-first inline exception; keep prepare-all-before-load, enforced constraints, named failures and existing transaction rules. Recommended: uses supported SQL and preserves intended enforcement.
- Require the literal phase on every dialect and remove DuckDB from this issue's supported constraint targets. Changes scope and leaves DuckDB unchanged.
- Defer the issue until DuckDB supports the required ALTER operation. No speculative drop/recreate workaround is recommended.

Concrete partial work: safe constraint derivation/rendering and bounded schema-preparation orchestration are committed on lane/W9-04; 77 focused tests pass. Writer integration and full acceptance remain incomplete. No constraint gate, assertion, or tolerance was weakened.

## #8 W9-07 — blocked on the earlier W9-04 decision

Status: parked on the preceding #5 Owner decision. Wanted2 explicitly reuses W9-04's constraint derivation, and the run requires an earlier issue to merge before a dependent one. W9-04 cannot currently merge under its literal DuckDB phase requirement.

Question: approve the recommended DuckDB exception for #5 so its safe constraint helper can merge, then merge the already-stacked helper into main before #8?

Recommendation: resolve #5 and merge it first, then finish targeted local acceptance and require full exact-head CI. The actual #5 helper is now stacked and its AST integration tests pass; no helper was duplicated and arbitrary check SQL remains refused. Unsupported native feature combinations use the already-authorized capability-refusal rules in Wanted8/#7Wanted6, requiring no additional Owner amendment. Independently implemented Delta features and their review fixes remain preserved on lane/W9-07; no ready-to-merge claim.
