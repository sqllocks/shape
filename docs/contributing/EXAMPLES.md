# Run the documentation examples

Use the repository build to check the commands and transcripts on the reference pages.

Status: available.

The starter tutorials use seeded retail data and compare their complete output exactly. They need local files, Git and DuckDB. They use the same public command line that you install from PyPI.

The reference examples also use local inputs. Their fixture script creates disposable CSV, Parquet, Excel, schema, contract and registry files. These are test inputs, not production captures or upstream reference datasets. The healthcare examples use the plugin's committed test fixtures and published excerpts; their small row counts describe those fixtures. Do not confuse them with a full downloaded code set.

## The source environment

The documentation workflow installs the core in editable mode and every plugin under `plugins/` in editable mode. It installs the versions in `requirements-docs.txt` for reproducible tooling. No Shape distribution is fetched from PyPI for these checks. This keeps the tests attached to the code in the pull request.

The harness sets `SHAPE_DOCS_REPO` to the checkout and runs each page in a fresh temporary directory. A page starts by calling `scripts/docs_example_setup.py` with its filename. Commands then run in page order. The fixture files persist between those commands and are removed with the temporary directory afterward.

Some reference commands build wheels or install a sample plugin. Those examples get an isolated Python environment. Release-preparation commands change only a disposable copy of the repository. The release-check transcript shows the actual diagnostics; running an example does not approve a release, publish an archive or create a repository tag.

## Local services and dependencies

Notifications use a receiver bound to loopback. It accepts the request and forwards nothing. Database examples that run in CI use DuckDB files. The dbt examples use dbt's DuckDB adapter and pinned public package sources prepared by `scripts/docs_dbt_packages.py`; they do not need a warehouse account. Those package sources are copied into the disposable fixture so dbt can resolve them locally.

The container example builds the repository's Dockerfile and profiles a mounted Parquet file. `DOCS_CA_BUNDLE` points to the environment's CA bundle. `DOCS_PROXY_IP` supplies the proxy host address when a session uses a proxy; the test environment uses loopback when no proxy is configured. The CA is a BuildKit secret mount and is not copied into an image layer. Configure your own environment rather than copying another machine's proxy settings.

## Reading the evidence

Every runnable reference command has a collapsible output block and an exit code. A nonzero exit can demonstrate a refusal, a failed quality gate or an incomplete maintainer check. Read the diagnostic and the surrounding explanation. A transcript proves what that run did; it does not turn a failure into a pass.

Timing, temporary directory names, job identifiers, freshly generated signing keys and environment information can vary. Reference tests validate the declared runtime fields and compare the remaining transcript exactly. They keep dataset values, row counts, findings, scores, errors and exit codes in the comparison. The page still displays the real recorded values. Starter tutorials do not use this runtime comparison: their transcripts match character for character.

## Account examples

An example that connects to an external platform keeps its command and a visible account label. Those examples are not run in CI. A maintainer supplies their transcript from an account before the owner note can be resolved. Local dry runs remain runnable examples when the command does not connect to the platform.

## Related

[Documentation style](DOCS_STYLE.md) · [Contributing](../../CONTRIBUTING.md) · [Starter tutorials](../TUTORIAL.md)
