# Shared CI repair

PR: https://github.com/sqllocks/shape/pull/40

Acceptance output:

- Before: certifi2026.7.22 audit exit1, `Found 1 known vulnerability in 1 package`: GHSA-6q48-6c7w-9gw2; fixed version2026.10.19.
- After: certifi2026.10.19 audit exit0, `No known vulnerabilities found`.
- Existing branding test failed the Host Grotesk assertion in [baseline CI](https://github.com/sqllocks/shape/actions/runs/39483319987/job/118837230960):1failed165passed. With prepared Playwright browser selection, targeted test output: `1 passed in 30.95s`. Every existing assertion remains unchanged.

Green exact-head CI links: pending; no full-suite pass claimed.
