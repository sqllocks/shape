#!/usr/bin/env bash
# Section 1.2 of the plan: the pinned RefEngine baseline (T-20), from a fresh machine.
#
#   source scripts/env.sh && bash benchmarks/vs_refengine/setup_refengine.sh
#
# Clones RefEngine 3.0.1 (git 422e78df) into $REFENGINE_ROOT, builds the RefEngine venv, and writes
# `pip freeze` to $BENCH_OUT_DIR/refengine_freeze.txt. Nothing under $REFENGINE_ROOT is modified
# (the checkout is only read; the editable install writes its egg-info elsewhere).
#
# REFENGINE_NAME (the reference engine's short name) is set outside the repository; the engine's
# import package (sqllocks_<name>) and its git URL (github.com/sqllocks/<name>) are derived from it.
set -euo pipefail

: "${REFENGINE_NAME:?REFENGINE_NAME is not set: export the short name of the reference engine, set outside the repository (see scripts/env.sh)}"
REFENGINE_PACKAGE="sqllocks_${REFENGINE_NAME}"
REFENGINE_REPO_URL="https://github.com/sqllocks/${REFENGINE_NAME}"
: "${REFENGINE_ROOT:=$HOME/refengine}"
: "${REFENGINE_VENV:=$HOME/.venvs/refengine}"
: "${BENCH_OUT_DIR:=$HOME/bench-out}"
REFENGINE_PY="$REFENGINE_VENV/bin/python"
PIN=422e78df2267e73bb2fa976267e48cb437861e2f

if [ ! -d "$REFENGINE_ROOT/.git" ]; then
    git clone "$REFENGINE_REPO_URL" "$REFENGINE_ROOT"
fi
git -C "$REFENGINE_ROOT" checkout "$PIN"

python3 -m venv "$REFENGINE_VENV"
"$REFENGINE_PY" -m pip install -U pip
"$REFENGINE_PY" -m pip install "pandas==3.0.6" "numpy==2.4.6" "scipy==1.17.1" \
    "pyarrow==25.0.1" "click==8.5.0" "requests==2.34.2" "python-dateutil==2.9.0.post0" \
    "pyyaml>=6.0,<7" "scikit-learn>=1.3,<2" "psutil"
"$REFENGINE_PY" -m pip install --no-deps -e "$REFENGINE_ROOT"
"$REFENGINE_PY" -c "import ${REFENGINE_PACKAGE}; print('refengine ok')"

mkdir -p "$BENCH_OUT_DIR"
"$REFENGINE_PY" -m pip freeze > "$BENCH_OUT_DIR/refengine_freeze.txt"
echo "wrote $BENCH_OUT_DIR/refengine_freeze.txt"
