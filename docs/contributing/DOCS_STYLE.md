# Documentation style

Status: available.

Write from this repository's code and observed behavior. Use plain language, second person,
present tense and short sentences. Use the reader's words: null, schema, drift, contract and CI.
Avoid seamless, revolutionary, unlock, leverage, magic, insights and AI-powered.

## Status

Use **available**, **experimental** or **coming** near the top of each page and for features with a different status. Available means shipped; it does not promise a 1.x compatibility contract. Use the shared early-access statement for version and stability pages. Describe 1.x guarantees as future policy.

## Tutorial template

1. Title and one-line purpose.
2. What you'll learn.
3. Prerequisites, including links to actual setup pages.
4. Time, stated as a reading estimate.
5. Numbered steps. Each shell command uses a `bash {.runnable}` fence. Follow it immediately with a collapsible `Output (exit N)` block containing the complete real combined output in a `text {.expected}` fence. Use `(no output)` for an empty stream.
6. What's next.
7. Related.

Each tutorial starts in an empty directory. Commands run in order. The pytest harness creates a temporary directory per page, runs the exact commands, checks the exit code and compares the entire output. Starter transcripts compare every character. Reference examples retain complete real transcripts too. The reference harness validates declared runtime fields (elapsed time, temporary paths, generated job IDs and signing keys) by type, then compares all remaining text exactly. Never normalise data values, findings, scores or errors. Explain the variable fields beside the examples. Use small local files and DuckDB. Do not require accounts. Configuration examples are labelled as configuration, not shell commands.

## Evidence and links

Run commands exactly as published. Keep account-dependent examples and label them "Needs a <platform> account. Not run in CI." Record unresolved work in HTML owner comments, which are inventoried in the PR description and block tag deployment. Do not invent sources, dates, calibration, prices or roadmap. Use only committed benchmark numbers with the machine named. Do not compare products. Safe capture is data minimisation, not anonymisation. Describe vendor code as reads, writes or exports. Snowflake and Databricks are write targets.

Use support@shapedata.ai for public contact. The only Premium item is Industry Profile Packs, coming, healthcare first, linked to https://shapedata.ai/. Optional `post:` and `video:` tutorial front matter contains only published URLs; empty values render no links.

For reference pages, explain purpose, behavior, limits and related pages. Status labels and evidence rules still apply. Generated reference pages inherit these rules. A short index may link to detail; remove incomplete reference stubs from navigation.
