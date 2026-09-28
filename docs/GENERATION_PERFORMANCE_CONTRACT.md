# Generation Performance Contract

A generation benchmark MUST state:
- row count and sustained duration/batching;
- generated columns/evidence;
- whether strings are components or formatted;
- reference-asset type/version;
- key and relationship constraints;
- host/runtime;
- peak memory.

The high-throughput path MUST resolve LocationScope/reference assets before execution and MUST NOT make per-row network/geocoder calls. Composite/FK integrity should be structural in the execution plan.

Target: >=1,000,000 sustained rows/sec for a columnar combined workload containing composite-key components, a foreign key, and coherent address components (street number/name, city, state/province, county where applicable, postal code, latitude and longitude) on qualified hardware.
