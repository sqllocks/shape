# Proposal: FHIR DocumentReference output with attached plain text

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

Clinical data is not only coded rows. Progress notes, discharge summaries, referral letters and reports travel as documents, and in FHIR R4 a document is a `DocumentReference` whose `content.attachment` holds the text (inline as base64, or as a link to a `Binary`). Pipelines that index notes, extract entities from them, de-identify them or route them by type need synthetic FHIR data that contains such documents, tied to the patients and claims of the same dataset.

The FHIR writers of `plugins/shape-healthcare-standards` write no documents:

- **The resource types are fixed.** `TABLE_RESOURCES` (`plugins/shape-healthcare-standards/src/shape_healthcare_standards/fhir/resources.py:889`) maps `member` to `Patient`, `eligibility` to `Coverage`, `provider` to `Practitioner` and `Organization`, `medical_claim` to `Claim` and `ExplanationOfBenefit`, and `pharmacy_claim` to `MedicationDispense`. `resources_for` (`resources.py:898`) refuses any other table. There is no `DocumentReference`, `Composition` or `Binary`.
- **No narrative or text at all.** The validation record says every resource gets the `dom-6` warning because "the resources carry no generated HTML narrative" (`plugins/shape-healthcare-standards/tests/fhir/VALIDATION.md:45` and `77`).
- **No encounter.** No `Encounter` resource is written and no contract table holds encounters (`contract.py:74` to `255`: `member`, `eligibility`, `provider`, `medical_claim`, `medical_claim_line`, `claim_acknowledgment`, `claim_diagnosis`, `claim_procedure`, `pharmacy_claim`, `drug_reference`). So "link to the Encounter the emitter writes" is not possible today. A note can link to its patient (`patient_ref`, `resources.py:201`), its author (`Context.provider_ref`, `resources.py:144`) and a claim.
- **No notes table in the contract.** The input contract (`contract.py:74`, keys at `contract.py:273`) is the single source of the columns a domain pack must produce, and it has no table for notes or other documents.

What exists and is reused: deterministic ids from source keys (`make_id`, `resources.py:74`), the reference rule of the module docstring (a typed `Type/id` reference only when the target's source row is known, otherwise an identifier-only logical reference, `resources.py:4` to `7`), companion tables through the `tables` option (`fhir/sink.py:32` to `40`), the three outputs that share `resource_stream` (the `fhir-ndjson` and `fhir-bundle` sinks and the `fhir` emitter, `fhir/emitter.py:24` to `32`), and the official-validator test with its record (`tests/fhir/test_official_validator.py`, `VALIDATION.md`).

## 2. Proposed design

### a. A `clinical_note` contract table

| Column | Type | Required | Meaning |
|---|---|---|---|
| `note_id` | string | yes | unique note id (the key) |
| `member_id` | string | yes | the patient (`member.member_id`) |
| `note_date` | timestamp | yes | when the note was written (`DocumentReference.date`) |
| `type_code`, `type_system`, `type_display` | string | code yes | document type (`DocumentReference.type`); `type_system` defaults to LOINC's system URI; Shape ships no codes, the table supplies them |
| `category_code` | string | no | `DocumentReference.category`; default `clinical-note` of the US Core category code system |
| `author_npi` | string | no | the author, resolved through the `provider` companion |
| `claim_id` | string | no | the claim the note supports (`context.related`) |
| `encounter_id` | string | no | an encounter key (`context.encounter`, as a logical reference, section 2c) |
| `status`, `doc_status` | string | no | `current` and `final` by default |
| `title` | string | no | `content.attachment.title` |
| `text` | string | yes | the plain text of the note |
| `content_type`, `language` | string | no | `text/plain; charset=utf-8` and the run's `language` option (a BCP 47 tag) by default |

The table is generic: any notes table with these columns works, whatever generated the text.

### b. The `DocumentReference` mapping

```json
{"resourceType": "DocumentReference", "id": "note-000123",
 "meta": {"profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-documentreference"]},
 "identifier": [{"system": "urn:shape:healthcare:note-id", "value": "N000123"}],
 "status": "current", "docStatus": "final",
 "type": {"coding": [{"system": "http://loinc.org", "code": "<type_code>", "display": "<type_display>"}]},
 "category": [{"coding": [{"system": "http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "code": "clinical-note"}]}],
 "subject": {"reference": "Patient/M0001"},
 "date": "2024-03-05T14:20:00Z",
 "author": [{"reference": "Practitioner/1234567893"}],
 "content": [{"attachment": {"contentType": "text/plain; charset=utf-8", "language": "en-US",
                             "data": "<base64 of the UTF-8 text>", "size": 1834, "hash": "<base64 SHA-1>",
                             "title": "Progress note", "creation": "2024-03-05T14:20:00Z"}}],
 "context": {"encounter": [{"identifier": {"system": "urn:shape:healthcare:encounter-id", "value": "E0042"}}],
             "related": [{"reference": "Claim/C0042"}]}}
```

- `data` is the base64 of the UTF-8 bytes of `text`; `size` is the byte count and `hash` the base64 SHA-1 of those bytes, as R4 defines them. The same row gives the same bytes.
- `subject` is `Patient/<id>` from `patient_ref`, as the other resources do. `author` is typed when the provider row is known, else an NPI identifier, through `Context.provider_ref`. `context.related` is `Claim/<id>` when the claim row is in the `tables` companions, else an identifier.
- Option `attachment`: `inline` (default) or `binary`. With `binary`, each note also yields a `Binary` resource (`contentType`, `data`) and the attachment carries `url: "Binary/<id>"` instead of `data`; `size` and `hash` stay. This keeps NDJSON lines short for long documents.
- Option `narrative`: `none` (default, as today) or `text`, which adds `text.status: "generated"` with the note's text escaped inside an XHTML `div`, for consumers that read the narrative.

### c. Encounters, and optionally Composition

- Phase 1 writes `context.encounter` as an identifier-only reference when `encounter_id` is set, following the module's rule of never writing a typed reference to a resource nobody emitted.
- Phase 2 (optional, the lead decides): an `encounter` contract table (`encounter_id`, `member_id`, `start`, `end`, `class_code`, `type_code`, `provider_npi`) mapped to `Encounter`, after which `context.encounter` becomes `Encounter/<id>` when the row is known.
- Phase 2 (optional): with `document_bundles: true`, the `fhir-bundle` sink writes one `document` Bundle per note: a `Composition` first (`status`, `type`, `subject`, `date`, `author`, `title`, one section whose narrative is the escaped text, as R4 requires for a section), then the referenced `Patient` and author resources.

## 3. Compatibility

- **Additive.** `clinical_note` is a new contract table and `DocumentReference` (and `Binary`) new entries in `TABLE_RESOURCES`. Every existing table produces byte-identical output. The error for an unknown table lists one more name.
- **Contract.** The contract is the documented source of truth for column names and types (`plugins/shape-healthcare-standards/README.md`), so the new table is a plugin API change recorded in `docs/plugins/stability.md` as additive. A pack that has no notes table is unaffected.
- **Options.** `attachment`, `narrative`, `language` and `document_bundles` are new options. The FHIR outputs read options with `options.get` and ignore unknown ones (`fhir/sink.py:52`, `fhir/sink.py:76` to `80`). That is safe for the first three: they only apply to `clinical_note`, which an older version refuses outright (`resources_for`, `resources.py:898`). `document_bundles` on existing tables would be ignored silently by an older version; it changes no existing table's output, so this is acceptable, but it is noted in the documentation.
- **Validation.** The test-only `fhir.resources` models used by the unit tests are R4B (`tests/fhir/test_fhir_resources.py:10`) while the output declares R4 4.0.1; `DocumentReference` differs little between them, and the official validator run with `-version 4.0.1` remains the authority, as it is for the existing resources.
- **Generation** is untouched: no generator version or seeded output changes.

## 4. Wanted (ready to paste into the issue)

Phase 1, DocumentReference:
1. A `clinical_note` contract table with the columns of section 2a and key `note_id`, checked like the other tables (types, required columns, duplicate keys).
2. A `DocumentReference` mapping as in section 2b (US Core DocumentReference profile, base64 `data`, `size`, `hash`, `contentType`, `language`, `title`, `creation`; typed or identifier references by the module's rule).
3. Options `attachment` (`inline` or `binary`, the latter adding `Binary` resources) and `narrative` (`none` or `text`), and `language` (default `en-US`).
4. The `fhir-ndjson` and `fhir-bundle` sinks and the `fhir` emitter accept `clinical_note` (with `member`, `provider` and `medical_claim` as `tables` companions).
5. `testing.sample_tables()` gains a few notes with non-ASCII text, and the official-validator test validates them with base R4 and US Core 6.1.0; `VALIDATION.md` records the per-type verdict.
6. Documentation: the plugin README and `docs/plugins/healthcare-standards.md` describe the table, the mapping and the options.

Phase 2, optional:
7. An `encounter` contract table mapped to `Encounter`, with `context.encounter` typed when the row is known.
8. `document_bundles: true` on `fhir-bundle`: one `document` Bundle per note with a `Composition`.

Acceptance beyond section 13's common items: decoding `data` (or the `Binary`) gives exactly the note's `text`; `size` and `hash` match the bytes; every typed reference resolves to a resource in the same output and no typed reference points at a resource that was not written; the official validator reports no errors for `DocumentReference` and `Binary` (and `Encounter` and `Composition` in phase 2) beyond the documented warnings; the same rows give byte-identical files; existing tables give byte-identical output before and after the change.

## 5. Open questions for the lead
1. Whether the encounter table (item 7) belongs in this package, or waits for a domain pack that produces encounters.
2. Whether `text/plain` is the only content type in phase 1, or HTML and PDF attachments (as bytes from the table) are wanted too.
3. Whether the FHIR outputs should start refusing unknown options (today they ignore them), which would be a behavior change for existing callers.
