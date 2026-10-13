# Proposal: a generic nested document (XML) writer

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`. Lower priority: a short proposal.

## 1. The problem

Many systems exchange nested documents: an order with its lines, a policy with its coverages, a customer with addresses, as XML or nested JSON. Testing an interface or a migration that reads them needs generated documents in that shape.

- Core sinks are flat: `csv`, `parquet`, `jsonl`, `ipc`, `tsv`, `sql`, `excel`, `delta`, `abfss`, `fabric-mirror` (`pyproject.toml:176` to `186`). `jsonl` writes one flat row per line.
- The reverse exists: the `json` and `xml` sources turn nested documents into related tables with `_id`, `_parent_id` and `_ordinal` (`src/shape/io/nested.py:1` to `15`, `docs/SOURCES.md`).
- The only nested writers are domain-specific (`fhir-bundle`, `fhir-ndjson` and X12 in `plugins/shape-healthcare-standards`). There is no generic one.

## 2. Proposed design

A sink `xml` (and `json-nested` from the same code) driven by a document map that nests child tables under their parent through declared relationships:

```json
{"format": "shape-document-map", "version": 1,
 "root": {"element": "Orders"},
 "record": {"table": "order", "element": "Order", "attributes": {"id": "order_id"},
            "fields": {"Date": "order_date", "Customer": "customer_id"},
            "children": [{"table": "order_line", "via": "order_id", "element": "Line",
                          "order_by": "line_no", "fields": {"Sku": "sku", "Qty": "quantity"}}]},
 "split": {"records_per_file": 10000}}
```

- Columns become elements or attributes; nulls are omitted or written with `xsi:nil` (`"nulls": "omit"|"nil"`). Values are written with a fixed format per Arrow type (ISO dates, plain decimals), escaped, in UTF-8.
- Child rows are grouped by the relationship key and ordered by `order_by`. Without a document map, the map is derived from the schema's relationships (each root table a record, children nested by foreign key).
- Optional namespaces and a declaration; output is streamed per record so memory is bounded by one record's children.
- The output read back with `flatten="tables"` gives the original tables (the round trip is the test).

## 3. Compatibility

New sink and new `shape-document-map` format (`format`, integer `version`, registered in `src/shape/compat.py`). No existing output or generator changes. XML writing must not need a dependency outside the standard library.

## 4. Wanted (ready to paste into the issue)

1. A document map format `shape-document-map` version 1 (`root`, `record`, `children` with `via` and `order_by`, `fields`, `attributes`, `nulls`, `namespaces`, `split`), validated strictly.
2. Sinks `xml` and `json-nested` (registered in `shape.sinks`), streaming per record, deterministic bytes.
3. A map derived from the schema's relationships when none is given.
4. Documentation in `docs/SINKS.md` with an order and lines example.

Acceptance beyond section 13's common items: writing then reading back with the `xml` source and `flatten="tables"` gives the same rows, keys and child order; the same input gives byte-identical files; output is well formed for values with markup characters and non-ASCII text.

## 5. Open questions for the lead
1. Whether validation against an XML Schema (XSD) the user supplies belongs here, or stays out (it needs a dependency).
2. Whether this is part of W5-07 (nested and standards sources) as its write side.
