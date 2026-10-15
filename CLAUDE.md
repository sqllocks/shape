Set up development with `make bootstrap` (uv 0.8.17, locked sync; Python 3.12+ for docs and Rust 1.85+).
Run tools with `uv run --locked` (select `--group docs` for documentation tools).
Never use `pip install` in the development environment. Change dependencies in
`pyproject.toml`, run `uv lock`, and commit `uv.lock` in the same commit.
Keep published dependency ranges unchanged when editing development groups.

Development setup and required checks are documented in [CONTRIBUTING.md](CONTRIBUTING.md).

The regression harness uses the last published Shape release (FRZ-01); the reference engine is archived. See [the harness README](benchmarks/vs_refengine/README.md).
