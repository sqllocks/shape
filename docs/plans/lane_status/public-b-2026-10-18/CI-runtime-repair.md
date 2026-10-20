# Shared CI repair

PR: https://github.com/sqllocks/shape/pull/40

Acceptance output:

- Before: certifi 2026.7.22 audit exit 1, `Found 1 known vulnerability in 1 package`: GHSA-6q48-6c7w-9gw2; fixed version 2026.10.19.
- After: certifi 2026.10.19 audit exit 0, `No known vulnerabilities found`.
- Existing branding test failed the Host Grotesk assertion in [baseline CI](https://github.com/sqllocks/shape/actions/runs/39483319987/job/118837230960): 1 failed, 165 passed. With prepared Playwright browser selection, targeted test output: `1 passed in 30.95s`. Every existing assertion remains unchanged.

Merged through PR40 as `53d3d0c1a740fcca0665c216c3ebdabaaac95e49`, after independent approval of exact source head `3f6254768085d6f96683550c8939998170be989f`.

All triggered source-head workflows completed green:

- [CI](https://github.com/sqllocks/shape/actions/runs/39491203122), all 27 jobs successful.
- [Documentation](https://github.com/sqllocks/shape/actions/runs/39491203111), verify successful; publish structurally inapplicable to pull requests.
- [Security](https://github.com/sqllocks/shape/actions/runs/39491203106).
- [DCO](https://github.com/sqllocks/shape/actions/runs/39491202974).

Merged-head main workflows are still running; no completed main-green claim yet.
