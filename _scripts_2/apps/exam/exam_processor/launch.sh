#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${1:-}" = "--dev" ]; then
    shift
    exec "$SCRIPT_DIR/dev.sh" "$@"
fi
source "$SCRIPT_DIR/../../_bootstrap.sh"
exec "$STUDY_TOOLS_PYTHON" -m _scripts_2.apps.exam.exam_processor.launcher "$@"
