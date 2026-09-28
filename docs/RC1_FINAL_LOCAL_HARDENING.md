# RC-1 Final Local Hardening

## Additional work completed
- Added malformed/newer `.shape` format rejection regression.
- Added classified-value release tests across many PII-like values.
- Added 100k-key bounded partition-state stress regression and stable partition restore regression.
- Parsed every Python source/test file with the AST parser: zero syntax failures.
- Scanned source/config/docs for common AWS, GitHub, Azure connection-string and private-key patterns: zero hits.
- Verified an isolated copied package can import `shape`, profile data, save `.shape`, load it, and report `1.0.0rc1`.
- Re-ran CLI help, doctor and conformance flows successfully.
- Added a 2M-event bounded-state soak harness.
- Added Capture -> Generate -> Capture reconstruction-fidelity harness.

## Final local qualification
- 118 tests pass (one expected duplicate-ZIP-member warning in the corruption hardening test).
- 73 requirements validate.
- Secret scan passes.
- 100 randomized stream boundary trials pass with zero failures.
- Source/test compile pass.
- Text + semantic profiling median: ~1.77M rows/sec on the current final run.
- Full mixed evidence engine: ~1.62M rows/sec on the current final run.
- Numeric reconstruction fidelity: mean relative error ~0.11%; variance relative error ~13.81%, both within the existing 5% / 15% tolerances.

## Important fidelity finding
The reconstruction test passes the current numeric contract, but the variance result is close enough to the 15% tolerance that generation fidelity should remain a GA-hardening area. RC-1 should expose fidelity certificates rather than imply perfect reconstruction.

## External/unavailable gates
Still not executable in this environment:
- real Kafka broker integration and failure/rebalance qualification;
- real Azure Event Hubs integration and failure/partition qualification;
- PyArrow-specific test matrix;
- wheel/sdist build because hatchling/build are unavailable and package installation cannot be fetched here;
- network-backed dependency vulnerability database audit;
- Linux/macOS/Windows clean-install CI matrix;
- cryptographic signing/provenance publication.

These remain explicit release-environment gates and are not marked passed.
