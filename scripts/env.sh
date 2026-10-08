# Source from the repo root: `source scripts/env.sh`
#
# REFENGINE_NAME is required and is NOT set here: it is the reference engine's short name, set
# outside the repository (your shell; in CI, the GitHub repository variable REFENGINE_NAME).
# The engine's import package, generator class, console script, event-field prefix, default
# output directory and git URL are all derived from it (benchmarks/vs_refengine/_refpkg.py).
if [ -z "${REFENGINE_NAME:-}" ]; then
    echo "scripts/env.sh: REFENGINE_NAME is not set (the reference engine's short name, set outside the repository); the comparison harness needs it" >&2
fi
export SHAPE_ROOT="$PWD"                                  # repo root
export REFENGINE_ROOT="${REFENGINE_ROOT:-$HOME/refengine}"      # pinned RefEngine checkout
export REFENGINE_VENV="${REFENGINE_VENV:-$HOME/.venvs/refengine}"
export REFENGINE_PY="$REFENGINE_VENV/bin/python"
export SHAPE_VENV="${SHAPE_VENV:-$HOME/.venvs/shape}"
export BENCH_DATA_DIR="${BENCH_DATA_DIR:-$HOME/bench-data}"  # generated datasets (not in git)
export BENCH_OUT_DIR="${BENCH_OUT_DIR:-$HOME/bench-out}"     # scratch output (not in git)
