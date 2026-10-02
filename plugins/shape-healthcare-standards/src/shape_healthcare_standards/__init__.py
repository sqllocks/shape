"""Shape plugin: healthcare standard outputs.

* ``shape.sinks``: ``x12-837p``, ``x12-837i`` (claims), ``x12-835`` (remittance), ``x12-834``
  (enrollment), ``fhir-ndjson`` (Bulk FHIR), ``fhir-bundle`` (one JSON bundle per file),
  ``omop`` (OMOP CDM v5.4 tables) and ``ncpdp`` (a bring-your-own-specification mapping layer).
* ``shape.emitters``: ``fhir`` sends FHIR resources as events.

The writers read the payer tables described in :mod:`shape_healthcare_standards.contract`.
Heavy libraries are never imported here; the validators used by the tests are test-only.
"""

SHAPE_API = "1.0"
