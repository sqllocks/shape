# Release Readiness — 0.12

Local executable evidence:
- pytest: 84 passed, 0 failed (1 warning from deliberately constructing a duplicate ZIP member in a security test).
- requirement registry: 67 requirements OK.
- basic secret scan: PASS.
- built-in conformance: artifact/capture/generation PASS.
- completed local benchmark: 250,000 rows; generation ~62,792 rows/sec; capture ~46,030 rows/sec; max RSS ~157,220 KB on this host.
- attempted 1,000,000-row materialized benchmark exceeded the execution window; no 1M claim is made.

Before public 1.0, run the retained Arrow suite in the declared dependency environment, exercise managed-service integrations, perform independent security/privacy assessment, settle production geography redistribution, and obtain deployment-specific authorization where required.
