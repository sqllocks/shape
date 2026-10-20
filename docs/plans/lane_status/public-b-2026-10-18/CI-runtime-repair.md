# Shared CI repair

PR: https://github.com/sqllocks/shape/pull/40

Acceptance output:

- Before: certifi 2026.7.22 audit exit 1, `Found 1 known vulnerability in 1 package`: GHSA-6q48-6c7w-9gw2; fixed version 2026.10.19.
- After: certifi 2026.10.19 audit exit 0, `No known vulnerabilities found`.
- Existing branding test failed the Host Grotesk assertion in [baseline CI](https://github.com/sqllocks/shape/actions/runs/39483319987/job/118837230960): 1 failed, 165 passed. With prepared Playwright browser selection, targeted test output: `1 passed in 30.95s`. Every existing assertion remains unchanged.

Green exact-head CI links: pending; no full-suite pass claimed.
