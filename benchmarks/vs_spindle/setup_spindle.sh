#!/usr/bin/env bash
# Section 1.2 of the plan: the pinned Spindle baseline (T-20), from a fresh machine.
#
#   source scripts/env.sh && bash benchmarks/vs_spindle/setup_spindle.sh
#
# Clones Spindle 3.0.1 (git 422e78df) into $SPINDLE_ROOT, builds the Spindle venv, and writes
# `pip freeze` to $BENCH_OUT_DIR/spindle_freeze.txt. Nothing under $SPINDLE_ROOT is modified
# (the checkout is only read; the editable install writes its egg-info elsewhere).
set -euo pipefail

: "${SPINDLE_ROOT:=$HOME/spindle}"
: "${SPINDLE_VENV:=$HOME/.venvs/spindle}"
: "${BENCH_OUT_DIR:=$HOME/bench-out}"
SPINDLE_PY="$SPINDLE_VENV/bin/python"
PIN=422e78df2267e73bb2fa976267e48cb437861e2f

if [ ! -d "$SPINDLE_ROOT/.git" ]; then
    git clone https://github.com/sqllocks/spindle "$SPINDLE_ROOT"
fi
git -C "$SPINDLE_ROOT" checkout "$PIN"

python3 -m venv "$SPINDLE_VENV"
"$SPINDLE_PY" -m pip install -U pip
"$SPINDLE_PY" -m pip install "pandas==3.0.6" "numpy==2.4.6" "scipy==1.17.1" \
    "pyarrow==25.0.1" "click==8.5.0" "requests==2.34.2" "python-dateutil==2.9.0.post0" \
    "pyyaml>=6.0,<7" "scikit-learn>=1.3,<2" "psutil"
"$SPINDLE_PY" -m pip install --no-deps -e "$SPINDLE_ROOT"
"$SPINDLE_PY" -c "import sqllocks_spindle; print('spindle ok')"

mkdir -p "$BENCH_OUT_DIR"
"$SPINDLE_PY" -m pip freeze > "$BENCH_OUT_DIR/spindle_freeze.txt"
echo "wrote $BENCH_OUT_DIR/spindle_freeze.txt"
