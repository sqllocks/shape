# Shape Tutorial

1. `shape capture data.csv -o baseline.json`
2. `shape show baseline.json`
3. `shape key data.csv id`
4. `shape fd data.csv --determinant postal_code --dependent state`
5. `shape quality data.csv --reference baseline.json`
6. Capture a later extract and run `shape diff baseline.json later.json`.
7. Run `shape fidelity baseline.json synthetic.csv --tolerance 0.10`.
8. Store versioned contracts in source control and use non-zero CLI exit codes as CI/ETL gates.

A Shape is evidence about behavior, not a copy of source rows. Sensitive evidence still requires governance because aggregates and distributions can leak information.
