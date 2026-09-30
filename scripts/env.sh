# Source from the repo root: `source scripts/env.sh`
export SHAPE_ROOT="$PWD"                                  # repo root
export SPINDLE_ROOT="${SPINDLE_ROOT:-$HOME/spindle}"      # pinned Spindle checkout
export SPINDLE_VENV="${SPINDLE_VENV:-$HOME/.venvs/spindle}"
export SPINDLE_PY="$SPINDLE_VENV/bin/python"
export SHAPE_VENV="${SHAPE_VENV:-$HOME/.venvs/shape}"
export BENCH_DATA_DIR="${BENCH_DATA_DIR:-$HOME/bench-data}"  # generated datasets (not in git)
export BENCH_OUT_DIR="${BENCH_OUT_DIR:-$HOME/bench-out}"     # scratch output (not in git)
