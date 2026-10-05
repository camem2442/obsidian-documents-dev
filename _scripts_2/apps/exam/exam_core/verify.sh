#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../_bootstrap.sh"
source "$SCRIPT_DIR/../../_verifier_runtime.sh"
MODE="$(validate_verifier_mode "${1:-standard}" quick standard full)"
"$STUDY_TOOLS_PYTHON" -m unittest discover -s "$SCRIPT_DIR/tests" -p 'test_*.py' -q
