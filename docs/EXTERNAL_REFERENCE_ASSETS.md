# External Reference Assets

Shape core is offline. Reference data is acquired separately, checksummed, licensed and then ingested locally.

- `load_geonames_postal()` ingests standard GeoNames postal TSV and records SHA-256 provenance. GeoNames' current postal-code download readme states CC BY 4.0 in its text (https://download.geonames.org/export/zip/readme.txt; the link beside it still names the 3.0 URL) and requires attribution.
- `load_census_gazetteer()` ingests Census Gazetteer state/county/place/ZCTA text files with version + SHA-256. ZCTA is Census statistical geography, not an exact synonym for USPS delivery ZIP geography.

Production Packs record source, retrieval/version, license, checksum, transformation version and sensitivity.
