#!/usr/bin/env bash
# SVG Converter Verifier
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/apps/_bootstrap.sh"
source "$ROOT_DIR/apps/_verifier_runtime.sh"
MODE="$(validate_verifier_mode "${1:-standard}" quick standard full)"

echo "==> [SVG Converter] Running verify ($MODE)..."

if [[ -d "$SCRIPT_DIR/tests" ]]; then
  "$STUDY_TOOLS_PYTHON" -m unittest discover -s "$SCRIPT_DIR/tests" -p 'test_*.py' -q
else
  "$STUDY_TOOLS_PYTHON" -m compileall -q "$SCRIPT_DIR"
fi

echo "==> [SVG Converter] All checks passed."
