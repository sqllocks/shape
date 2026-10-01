#!/usr/bin/env bash
# PF-03 `pure-wheel` job body: build the T-29 pure wheel, assert py3-none-any and < 28.6 MB,
# install it next to nothing but PyPI numpy, pyarrow and pandas, and run the UDF tests with
# SHAPE_KERNEL=python from a scratch directory (so the source tree cannot shadow the wheel).
# Usage: scripts/ci_pure_wheel.sh [python]   (Python 3.11 in CI)
set -euo pipefail
PY="${1:-python3}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

"$PY" "$ROOT/scripts/build_pure_wheel.py" --out "$WORK/dist"
WHEEL="$(ls "$WORK"/dist/*.whl)"
case "$WHEEL" in *-py3-none-any.whl) ;; *) echo "not py3-none-any: $WHEEL" >&2; exit 1 ;; esac
SIZE="$(stat -c %s "$WHEEL")"
[ "$SIZE" -lt 28600000 ] || { echo "wheel is $SIZE bytes, limit 28,600,000" >&2; exit 1; }
echo "wheel: $(basename "$WHEEL") $SIZE bytes"

"$PY" -m venv "$WORK/venv"
VPY="$WORK/venv/bin/python"
"$VPY" -m pip install -q -U pip
# the wheel's own requirements are numpy and pyarrow; pandas and pytest are the UDF test needs
"$VPY" -m pip install -q numpy pyarrow pandas pytest
"$VPY" -m pip install -q "$WHEEL"
cd "$WORK"
"$VPY" -c "import shape, sys; assert 'site-packages' in shape.__file__, shape.__file__; print('shape', shape.__version__)"
export SHAPE_KERNEL=python
"$VPY" -c "from shape.kernel import kernel_name; assert kernel_name() == 'python', kernel_name()"

# 1. the helpers, with no Fabric SDK present
"$VPY" -m pytest -q -p no:cacheprovider --rootdir "$ROOT" "$ROOT/tests/integrations"

# 2. the real function_app.py against the public SDK (needs unixODBC for pyodbc)
"$VPY" -m pip install -q fabric-user-data-functions nbformat
"$VPY" -m pytest -q -p no:cacheprovider --rootdir "$ROOT" "$ROOT/tests/demo/fabric/test_udf.py"
