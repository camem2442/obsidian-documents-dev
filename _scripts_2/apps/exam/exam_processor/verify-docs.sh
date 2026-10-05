#!/usr/bin/env bash
# Exam Processor Documentation Contract Verifier
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/apps/_bootstrap.sh"

echo "==> [Exam Processor Docs] Checking documentation integrity..."
"$STUDY_TOOLS_PYTHON" "$SCRIPT_DIR/scripts/verify_docs.py"
