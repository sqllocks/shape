# Shape Tutorial

1. `shape capture data.csv -o baseline.json` writes a model of the data (evidence, no rows).
2. `shape show baseline.json` prints it.
3. `shape key data.csv id` checks that `id` is a key.
4. `shape fd data.csv --determinant postal_code --dependent state` measures a functional dependency.
5. `shape quality data.csv --reference baseline.json` checks the data against rules inferred from the model.
6. Capture a later extract (`shape capture later.csv -o later.json`) and run
   `shape compatibility baseline.json later.json` to find removed and retyped columns (exit 5).
7. For drift in the values, profile both extracts and compare the profiles:
   `shape profile data.csv -o baseline.shape`, `shape profile later.csv -o later.shape`, then
   `shape diff baseline.shape later.shape --fail-on-drift` (exit 1 when drift is found;
   thresholds in `docs/DRIFT.md`).
8. Run `shape fidelity baseline.json synthetic.csv --tolerance 0.10` to certify a synthetic CSV
   against the captured model (exit 3 when it fails).
9. Store versioned contracts in source control and use non-zero CLI exit codes as CI/ETL gates.

A Shape is evidence about behavior, not a copy of source rows. Sensitive evidence still requires governance because aggregates and distributions can leak information.
