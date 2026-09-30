# BLOCKED: DM-00 (partial)

Status: **not done.** The §8.3 deletions have not been performed.

## Evidence

The session's permission classifier denied the deletion command as
"Irreversible Local Destruction". The command was `git rm` and `rm -rf` on `rq/`,
`docs/qualification/`, `docs/audit/`, the root `*_MANIFEST.json`,
`*_QUALIFICATION.json`, `GA_*` and `RC1_*` files, the non-keep-list files in
`docs/` and `docs/plans/`, and the workflows `external-connectors.yml`, `ga.yml`
and `release-ga.yml`. Nothing was removed.

## Done in this commit (not yet checked by the acceptance commands)

- `LICENSE` (MIT), `THIRD_PARTY_NOTICES.md` (GeoNames CC-BY-4.0), and the
  `license`/`license-files` lines of `pyproject.toml`.
- `README.md`, `CHANGELOG.md`, `SECURITY.md` rewritten.
- `tests/torture/test_all_modules.py` rewritten with `pkgutil.walk_packages`.

## Still to do

1. Perform the §8.3 deletions. Open question: keep
   `docs/plans/spindle_coverage.tsv`. The plan references it (lines 13, 192, 712),
   although §8.3 says to delete all of `docs/plans/` except `COMPLETION_PLAN.md`.
2. Run the DM-00 acceptance commands: the path checks, the P0-05 claims grep, the
   Apache grep and the full `pytest` suite.
3. Write `docs/plans/demo_status/DM-00.md`.

The repo must not be made public until step 2 passes.
