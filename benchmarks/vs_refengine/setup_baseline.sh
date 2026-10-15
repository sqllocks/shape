#!/usr/bin/env bash
# Install the declared published Shape release in an isolated environment.
set -euo pipefail
BASELINE_VERSION="${BASELINE_VERSION:-0.9.3}"
BASELINE_VENV="${BASELINE_VENV:-$HOME/.venvs/shape-$BASELINE_VERSION}"
BASELINE_PY="$BASELINE_VENV/bin/python"
BENCH_OUT_DIR="${BENCH_OUT_DIR:-$HOME/bench-out}"
BOOTSTRAP_PY="${PYTHON:-python3}"
"$BOOTSTRAP_PY" -m venv "$BASELINE_VENV"
baseline_python() { env -u PYTHONPATH -u PYTHONHOME "$BASELINE_PY" "$@"; }
packages=("sqllocks-shape==$BASELINE_VERSION" "sqllocks-shape-domains==$BASELINE_VERSION" "pyarrow==25.0.1")
installed=0
if [ -n "${BASELINE_INDEX:-}" ]; then
    if [ -d "$BASELINE_INDEX" ]; then
        baseline_python -m pip install --no-index --find-links "$BASELINE_INDEX" "${packages[@]}" && installed=1
    else
        "$BOOTSTRAP_PY" -c 'import sys,urllib.parse; p=urllib.parse.urlsplit(sys.argv[1]); sys.exit(2 if p.username or p.password or p.query or p.fragment or p.scheme not in ("http","https") else 0)' "$BASELINE_INDEX"
        baseline_python -m pip install --index-url "$BASELINE_INDEX" "${packages[@]}" && installed=1
    fi
fi
if [ "$installed" = 0 ]; then
    baseline_python -m pip install "${packages[@]}" && installed=1
fi
if [ "$installed" = 0 ]; then
    root="$(git rev-parse --show-toplevel)"
    source_dir="$BASELINE_VENV.source"
    git worktree add --detach "$source_dir" "v$BASELINE_VERSION"
    baseline_python -m pip install "$source_dir" "$source_dir/plugins/shape-domains" "pyarrow==25.0.1"
fi
found="$(baseline_python -c 'import shape; print(shape.__version__)')"
if [ "$found" != "$BASELINE_VERSION" ]; then
    echo "baseline version $found; required $BASELINE_VERSION" >&2
    exit 2
fi
if [ "${BASELINE_PURE:-0}" != 1 ]; then
    kernel="$(env -u PYTHONPATH -u PYTHONHOME "$BASELINE_VENV/bin/shape" doctor --json | "$BOOTSTRAP_PY" -c 'import json,sys; print(json.load(sys.stdin)["kernel"])')"
    if [ "$kernel" != rust ]; then
        echo "baseline kernel $kernel; rust required (BASELINE_PURE=1 permits pure correctness tests)" >&2
        exit 2
    fi
fi
mkdir -p "$BENCH_OUT_DIR"
baseline_python -m pip freeze > "$BENCH_OUT_DIR/baseline_freeze.txt"
