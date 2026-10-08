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
# PF-06: generation needs an installed domain, which is a plugin wheel (pure Python, about 2 MB)
"$PY" -m pip wheel -q --no-deps "$ROOT/plugins/shape-domains" -w "$WORK/plugins"
"$VPY" -m pip install -q --no-deps "$WORK"/plugins/sqllocks_shape_domains-*.whl
cd "$WORK"
"$VPY" -c "import shape, sys; assert 'site-packages' in shape.__file__, shape.__file__; print('shape', shape.__version__)"
export SHAPE_KERNEL=python
"$VPY" -c "from shape.kernel import kernel_name; assert kernel_name() == 'python', kernel_name()"

# 1. the helpers, with no Fabric SDK present. The distributed-profile tests (PF-02) also run
# Spark executors on this wheel's pure-Python kernel, so they need pyspark (and Java, which the
# runner has).
"$VPY" -m pip install -q "pyspark>=4.0,<5"
# The ADF batch gate tests (tests/integrations/test_adf_gate_errors.py) load
# integrations/adf/batch/run_gate.py, which imports fsspec for its storage (local paths here).
# Install it with the requirement the project's test dependencies (the [dev] extra) pin.
FSSPEC_REQ="$("$PY" -c 'import sys, tomllib
deps = tomllib.load(open(sys.argv[1], "rb"))["project"]["optional-dependencies"]["dev"]
print(next(d for d in deps if d.startswith("fsspec")))' "$ROOT/pyproject.toml")"
"$VPY" -m pip install -q "$FSSPEC_REQ"
# The Fabric notebooks' Delta writes (DEMO-LIVE F-1, tests/integrations/test_fabric_onelake.py)
# go through deltalake: install it with the requirement of the project's [delta] extra.
DELTA_REQ="$("$PY" -c 'import sys, tomllib
deps = tomllib.load(open(sys.argv[1], "rb"))["project"]["optional-dependencies"]["delta"]
print(next(d for d in deps if d.startswith("deltalake")))' "$ROOT/pyproject.toml")"
"$VPY" -m pip install -q "$DELTA_REQ"
"$VPY" -m pytest -q -p no:cacheprovider --rootdir "$ROOT" "$ROOT/tests/integrations"

# (tests/integrations includes the PF-06 generation helpers and generateSample, on the pure kernel)

# 2. the real function_app.py against the public SDK (needs unixODBC for pyodbc)
"$VPY" -m pip install -q fabric-user-data-functions nbformat
"$VPY" -m pytest -q -p no:cacheprovider --rootdir "$ROOT" "$ROOT/tests/demo/fabric/test_udf.py" \
    "$ROOT/tests/demo/fabric/test_generate_udf.py"
