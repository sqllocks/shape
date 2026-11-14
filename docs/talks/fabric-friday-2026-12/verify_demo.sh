#!/usr/bin/env bash
set -euo pipefail
TALK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$TALK/../../.."
UV_OFFLINE=1 uv run --locked --no-sync python "$TALK/verify_demo.py"
