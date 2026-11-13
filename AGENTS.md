Set up development with `make bootstrap` (uv 0.8.17, locked sync; Python 3.12+ for docs
and the Rust toolchain pinned in `rust-toolchain.toml`).
Run tools with `uv run --locked` (select `--group docs` for documentation tools).
Never use `pip install` in the development environment. Change dependencies in
`pyproject.toml`, run `uv lock`, and commit `uv.lock` in the same commit.
Keep published dependency ranges unchanged when editing development groups.

Development setup and required checks are documented in [CONTRIBUTING.md](CONTRIBUTING.md).
