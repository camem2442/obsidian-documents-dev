#!/usr/bin/env bash
# Exam Processor Verifier
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/apps/_bootstrap.sh"
source "$ROOT_DIR/apps/_verifier_runtime.sh"
MODE="$(validate_verifier_mode "${1:-standard}" quick standard full)"

echo "==> [Exam Processor] Running verify ($MODE)..."

# Quick: Python compile + Unittest
"$STUDY_TOOLS_PYTHON" -m unittest discover -s "$SCRIPT_DIR/tests" -p 'test_*.py' -q

if [[ "$MODE" == "quick" ]]; then
  echo "==> [Exam Processor] Quick check passed."
  exit 0
fi

# Standard / Full: Playwright browser smoke
PLAYWRIGHT_BIN="$ROOT_DIR/node_modules/.bin/playwright"
if [[ ! -x "$PLAYWRIGHT_BIN" ]]; then
  echo "BLOCKED_ENVIRONMENT: Playwright is required for Exam Processor in $MODE mode but '$PLAYWRIGHT_BIN' was not found." >&2
  echo "Action required: Run 'npm install' in _scripts_2 to install required browser test tools." >&2
  exit "$EXIT_BLOCKED_ENVIRONMENT"
fi

echo "==> [Exam Processor] Browser workflow tests"
npm run test:ui:exam --prefix "$ROOT_DIR"
node "$SCRIPT_DIR/tests/ui_focused_review.cjs"
node "$SCRIPT_DIR/tests/ui_sample_review.cjs"
node "$SCRIPT_DIR/tests/ui_bulk_approval.cjs"
node "$SCRIPT_DIR/tests/ui_evaluation.cjs"
node "$SCRIPT_DIR/tests/ui_offline_evaluation.cjs"
node "$SCRIPT_DIR/tests/ui_batch_jobs.cjs"
node "$SCRIPT_DIR/tests/ui_export_jobs.cjs"
node "$SCRIPT_DIR/tests/ui_input_jobs.cjs"
node "$SCRIPT_DIR/tests/ui_ux_flow.cjs"

echo "==> [Exam Processor] All checks passed."
